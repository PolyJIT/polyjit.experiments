"""
This experiment applies polly's transformations to all projects and measures
the runtime.
"""
from benchbuild import experiment, extensions, settings

CFG = settings.CFG


class Polly(experiment.Experiment):
    """The polly experiment."""

    NAME = "polly"

    def actions_for_project(self, project):
        """Compile & Run the experiment with -O3 enabled."""
        project.cflags = [
            "-O3", "-fno-omit-frame-pointer", "-Xclang", "-load", "-mllvm",
            "-stats", "-Xclang", "LLVMPolly.so", "-mllvm", "-polly"
        ]
        num_jobs = int(CFG['jobs'].value)
        project.runtime_extension = extensions.run.RuntimeExtension(
            project, self,
            {'jobs': num_jobs}) << extensions.time.RunWithTime()

        return self.default_runtime_actions(project)
