"""
The 'polly-openmp' Experiment.

This experiment applies polly's transformations with openmp code generation
enabled to all projects and measures the runtime.

This forms the baseline numbers for the other experiments.

Measurements
------------

3 Metrics are generated during this experiment:
    time.user_s - The time spent in user space in seconds (aka virtual time)
    time.system_s - The time spent in kernel space in seconds (aka system time)
    time.real_s - The time spent overall in seconds (aka Wall clock)
"""
import copy
import uuid

from benchbuild import experiment, extensions, settings

CFG = settings.CFG


class PollyOpenMP(experiment.Experiment):
    """Timing experiment with Polly & OpenMP support."""

    NAME = "polly-openmp"

    def actions_for_project(self, project):
        """Build & Run each project with Polly & OpenMP support."""
        project.ldflags = ["-lgomp"]
        project.cflags = [
            "-O3", "-Xclang", "-load", "-Xclang", "LLVMPolly.so", "-mllvm",
            "-polly", "-mllvm", "-polly-parallel"
        ]

        actns = []
        num_jobs = int(CFG['jobs'].value)
        for i in range(2, num_jobs + 1):
            project_i = copy.deepcopy(project)
            project_i.run_uuid = uuid.uuid4()
            project_i.runtime_extension = extensions.run.RuntimeExtension(
                project_i, self, {'jobs': i}) << extensions.time.RunWithTime()
            actns.extend(self.default_runtime_actions(project_i))

        return actns
