from functools import partial

from benchbuild import settings
from benchbuild.utils import run
from polyjit.experiments import polyjit

CFG = settings.CFG


class PJITRegression(polyjit.PolyJIT):
    """
    This experiment will generate a series of regression tests.

    This can be used every time a new revision is produced for PolyJIT, as
    it will automatically collect any new SCoPs detected, using the JIT.

    The collection of the tests itself is intgrated into the JIT, so this
    experiment looks a lot like a RAW experiment, except we don't run
    anything.
    """

    NAME = "pj-collect"

    def actions_for_project(self, project):

        def _track_compilestats(project, experiment, _, clang):
            """Compile the project and track the compilestats."""
            clang = clang["-mllvm", "-polli-collect-modules"]
            with run.track_execution(clang, project, experiment) as command:
                command()

        project = polyjit.PolyJIT.init_project(project)
        project.cflags = ["-DLIKWID_PERFMON"] + project.cflags
        project.compiler_extension = partial(_track_compilestats,
                                             project, self, CFG)
        return self.default_compiletime_actions(project)
