from benchbuild.extensions import run
from polyjit.experiments import compilestats, polyjit


class Compilestats(polyjit.PolyJIT):
    """Gather compilestats, with enabled JIT."""

    NAME = "pj-cs"
    SCHEMA = [compilestats.CompileStat.__table__]

    def actions_for_project(self, project):
        project = polyjit.PolyJIT.init_project(project)
        project.compiler_extension = \
            run.WithTimeout(compilestats.ExtractCompileStats(project, self))
        return self.default_compiletime_actions(project)
