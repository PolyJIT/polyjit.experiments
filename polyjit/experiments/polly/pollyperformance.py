"""
PollyPerformance experiment.
"""
import copy
import re
import uuid
import warnings

import benchbuild.settings as settings
from benchbuild.experiment import Experiment
from benchbuild.extensions import RuntimeExtension, RunWithTime

settings.CFG["perf"] = {
    "config": {
        "default": None,
        "desc": "A configuration for the pollyperformance experiment."
    }
}


class ShouldNotBeNone(RuntimeWarning):
    """User warning, if config var is null."""


class PollyPerformance(Experiment):
    """ The polly performance experiment. """

    NAME = "pollyperformance"

    def actions_for_project(self, project):
        configs = settings.CFG["perf"]["config"].value()
        if configs is None:
            warnings.warn("({0}) should not be null.".format(
                repr(settings.CFG["perf"]["config"])),
                          category=ShouldNotBeNone, stacklevel=2)
            return

        config_list = re.split(r'\s*', configs)

        config_with_llvm = []
        for config in config_list:
            config_with_llvm.append("-mllvm")
            config_with_llvm.append(config)

        project.cflags = ["-O3", "-fno-omit-frame-pointer",
                          "-Xclang", "-load",
                          "-Xclang", "LLVMPolyJIT.so",
                          "-mllvm", "-polly"] + config_with_llvm

        actns = []
        jobs = settings.CFG["jobs"].value()
        for i in range(1, int(jobs)):
            cp = copy.deepcopy(project)
            cp.run_uuid = uuid.uuid4()

            cp.cflags += ["-mllvm", "-polly-num-threads={0}".format(i)]
            cp.runtime_extension = \
                RunWithTime(
                    RuntimeExtension(cp, self, {'jobs': i}))

            actns.extend(self.default_runtime_actions(cp))

        return actns
