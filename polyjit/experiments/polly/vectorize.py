"""
The 'polly-vectorize' Experiment
====================

This experiment applies polly's transformations with stripmine vectorizer
enabled to all projects and measures the runtime.

This forms the baseline numbers for the other experiments.

Measurements
------------

3 Metrics are generated during this experiment:
    time.user_s - The time spent in user space in seconds (aka virtual time)
    time.system_s - The time spent in kernel space in seconds (aka system time)
    time.real_s - The time spent overall in seconds (aka Wall clock)
"""

from benchbuild import experiment, extensions, settings

CFG = settings.CFG


class PollyVectorizer(experiment.Experiment):
    """ The polly experiment with vectorization enabled. """

    NAME = "polly-vectorize"

    def actions_for_project(self, project):
        """Compile & Run the experiment with -O3 enabled."""
        project.cflags = [
            "-O3", "-fno-omit-frame-pointer", "-Xclang", "-load", "-Xclang",
            "LLVMPolyJIT.so", "-mllvm", "-polly", "-mllvm",
            "-polly-vectorizer=stripmine"
        ]

        num_jobs = int(CFG['jobs'].value)
        project.runtime_extension = extensions.run.RuntimeExtension(
            project, self,
            {'jobs': num_jobs}) << extensions.time.RunWithTime()
        return self.default_runtime_actions(project)
