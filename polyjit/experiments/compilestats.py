"""
The 'compilestats' experiment.

This experiment is a basic experiment in the benchbuild study. It simply runs
all projects after compiling it with -O3 and catches all statistics emitted
by llvm.

"""
import logging

import parse
import sqlalchemy as sa

from benchbuild import settings
from benchbuild.experiment import Experiment
from benchbuild.extensions import base, run
from benchbuild.utils import db
from benchbuild.utils import run as u_run
from benchbuild.utils import schema

settings.CFG["cs"] = {
    "components": {
        "default": None,
        "desc": "List of filters for compilestats components."
    },
    "names": {
        "default": None,
        "desc": "List of filters for compilestats names."
    }
}

LOG = logging.getLogger(__name__)
class ExtractCompileStats(base.Extension):
    """Extract LLVM's compilation stats.

    This extension extracts the output of LLVM's '-stats' option.
    You can control the tracked statistics by using the sections
    `cs.components` and `cs.names` in the configuration.

    Furthermore, this runs the compiler and tracks the state in the databse,
    similar to RunCompiler.
    """

    def __init__(self, project, experiment, *extensions, config=None):
        self.project = project
        self.experiment = experiment

        super(ExtractCompileStats, self).__init__(*extensions, config=config)

    @staticmethod
    def get_compilestats(prog_out):
        """ Get the LLVM compilation stats from :prog_out:. """

        stats_pattern = parse.compile("{value:d} {component} - {desc}\n")

        for line in prog_out.split("\n"):
            if line:
                try:
                    res = stats_pattern.search(line + "\n")
                except ValueError:
                    LOG.warning("Triggered a parser exception for: '%s'\n",
                                line)
                    res = None
                if res is not None:
                    yield res

    def __call__(self, cc, *args, project=None, **kwargs):
        if project:
            self.project = project

        original_command = cc[args]
        clang = cc["-Qunused-arguments"]
        clang = clang[args]
        clang = clang[project.cflags]
        clang = clang[project.ldflags]
        clang = clang["-mllvm", "-stats"]

        run_config = self.config
        session = schema.Session()
        with u_run.track_execution(clang, self.project, self.experiment) as _run:
            run_info = _run()
            if run_config is not None:
                db.persist_config(run_info.db_run, session, run_config)

            if not run_info.has_failed:
                stats = []
                cls = ExtractCompileStats
                for stat in cls.get_compilestats(run_info.stderr):
                    compile_s = CompileStat()
                    compile_s.name = stat["desc"].rstrip()
                    compile_s.component = stat["component"].rstrip()
                    compile_s.value = stat["value"]
                    stats.append(compile_s)

                components = settings.CFG["cs"]["components"].value
                names = settings.CFG["cs"]["names"].value

                stats = [s for s in stats if str(s.component) in components] \
                    if components is not None else stats
                stats = [s for s in stats if str(s.name) in names] \
                    if names is not None else stats

                if stats:
                    for stat in stats:
                        LOG.info(" [%s] %s = %s", stat.component, stat.name,
                                 stat.value)
                    db.persist_compilestats(run_info.db_run, run_info.session,
                                            stats)
                else:
                    LOG.info("No compilestats left, after filtering.")
                    LOG.warning("  Components: %s", components)
                    LOG.warning("  Names:      %s", names)
            else:
                with u_run.track_execution(original_command, self.project,
                                         self.experiment, **kwargs) as _run:
                    LOG.warning("Fallback to: %s", str(original_command))
                    run_info = _run()

        ret = self.call_next(cc, *args, **kwargs)
        ret.append(run_info)
        session.commit()
        return ret

    def __str__(self):
        return "Track compilation statistics"



class CompileStat(schema.BASE):
    __tablename__ = 'compilestats'

    run_id = sa.Column(
        sa.Integer,
        sa.ForeignKey("run.id", onupdate="CASCADE", ondelete="CASCADE"),
        index=True,
        primary_key=True)
    id = sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True)
    name = sa.Column('name', sa.String, index=True)
    component = sa.Column('component', sa.String, index=True)
    value = sa.Column('value', sa.Numeric)


class CompilestatsExperiment(Experiment):
    """The compilestats experiment."""

    NAME = "cs"
    SCHEMA = [CompileStat.__table__]

    def actions_for_project(self, project):
        project.compiler_extension = \
            run.WithTimeout(ExtractCompileStats(project, self))
        return CompilestatsExperiment.default_compiletime_actions(project)


class PollyCompilestatsExperiment(Experiment):
    """The compilestats experiment with polly enabled."""

    NAME = "p-cs"
    SCHEMA = [CompileStat.__table__]

    def actions_for_project(self, project):
        project.cflags = [
            "-O3", "-Xclang", "-load", "-Xclang", "LLVMPolly.so", "-mllvm",
            "-polly"
        ]
        project.compiler_extension = \
            run.WithTimeout(ExtractCompileStats(project, self))
        return CompilestatsExperiment.default_compiletime_actions(project)
