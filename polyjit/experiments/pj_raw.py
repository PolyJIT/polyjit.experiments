import copy
import uuid

from benchbuild import extensions, settings
from polyjit.experiments import polyjit

CFG = settings.CFG

class PJITRaw(polyjit.PolyJIT):
    """
    An experiment that executes all projects with PolyJIT support.

    This is our default experiment for speedup measurements.
    """

    NAME = "pj-raw"

    def actions_for_project(self, project):

        project = polyjit.PolyJIT.init_project(project)

        actns = []
        for i in range(2, int(str(CFG["jobs"])) + 1):
            cp = copy.deepcopy(project)
            cp.run_uuid = uuid.uuid4()
            cp.cflags += ["-mllvm", "-polly-num-threads={0}".format(i)]
            cp.runtime_extension = \
                extensions.run.RuntimeExtension(
                    cp, self, config={
                        "jobs": i,
                        "cores": str(i-1),
                        "cores-config": str(i),
                        "recompilation": "enabled"}) \
                << polyjit.EnablePolyJIT() \
                << polyjit.ClearPolyJITConfig() \
                << extensions.time.RunWithTime() \
                << polyjit.RegisterPolyJITLogs() \
                << extensions.log.LogAdditionals()

            actns.extend(self.default_runtime_actions(cp))
        return actns
