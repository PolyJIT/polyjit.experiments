"""
PollyPerformance experiment.
"""
import copy
import re
import uuid
import warnings

from benchbuild import experiment, extensions, settings

settings.CFG["perf"] = {
    "config": {
        "default": None,
        "desc": "A configuration for the pollyperformance experiment."
    }
}


class ShouldNotBeNone(RuntimeWarning):
    """User warning, if config var is null."""


class PollyPerformance(experiment.Experiment):
    """ The polly performance experiment. """

    NAME = "pollyperformance"

    def actions_for_project(self, project):
        configs = settings.CFG["perf"]["config"].value
        if configs is None:
            warnings.warn(
                "({0}) should not be null.".format(
                    repr(settings.CFG["perf"]["config"])),
                category=ShouldNotBeNone,
                stacklevel=2)
            return

        config_list = re.split(r'\s*', configs)

        config_with_llvm = []
        for config in config_list:
            config_with_llvm.append("-mllvm")
            config_with_llvm.append(config)

        project.cflags = [
            "-O3", "-fno-omit-frame-pointer", "-Xclang", "-load", "-Xclang",
            "LLVMPolyJIT.so", "-mllvm", "-polly"
        ] + config_with_llvm

        actns = []
        num_jobs = int(settings.CFG["jobs"].value)
        for i in range(1, num_jobs):
            project_i = copy.deepcopy(project)
            project_i.run_uuid = uuid.uuid4()

            project_i.cflags += ["-mllvm", "-polly-num-threads={0}".format(i)]
            project_i.runtime_extension = extensions.run.RuntimeExtension(
                project_i, self, {'jobs': i}) << extensions.time.RunWithTime()

            actns.extend(self.default_runtime_actions(project_i))

        return actns
