import copy
import uuid

import plumbum as pb
import sqlalchemy as sa

from benchbuild import extensions as ext
from benchbuild import likwid, settings
from benchbuild.utils import actions, db, schema
from benchbuild.utils.cmd import likwid_perfctr, rm
from polyjit.experiments import polyjit as pj

CFG = settings.CFG


class RunWithLikwid(ext.run.RuntimeExtension):
    """
    Run the given file wrapped by likwid.

    Args:
        project: The benchbuild.project.
        experiment: The benchbuild.experiment.
        config: The benchbuild.settings.config.
        jobs: Number of cores we should use for this exection.
        run_f: The file we want to execute.
        args: List of arguments that should be passed to the wrapped binary.
        **kwargs: Dictionary with our keyword args. We support the following
            entries:

            project_name: The real name of our project. This might not
                be the same as the configured project name, if we got wrapped
                with ::benchbuild.project.wrap_dynamic
            has_stdin: Signals whether we should take care of stdin.
    """

    def __call__(self, binary_command, *args, may_wrap=True, **kwargs):
        self.project.name = kwargs.get("project_name", self.project.name)

        likwid_f = self.project.name + ".txt"
        jobs = self.config['jobs']
        res = []
        for group in ["CLOCK"]:
            run_cmd = \
                likwid_perfctr["-O", "-o", likwid_f, "-m",
                               "-C", "0-{0:d}".format(jobs),
                               "-g", group, binary_command]

            with pb.local.env(POLLI_ENABLE_LIKWID=1):
                res.extend(self.call_next(run_cmd, *args, **kwargs))

            likwid_measurement = likwid.perfcounters(likwid_f)
            for run_info in res:
                persist_likwid(run_info.db_run, run_info.session,
                               likwid_measurement)
                db.persist_config(run_info.db_run, run_info.session, {
                    "cores": str(jobs),
                    "likwid.group": group
                })
            rm("-f", likwid_f)
        return res


class Likwid(schema.BASE):
    __tablename__ = 'likwid'

    metric = sa.Column(sa.String, primary_key=True, index=True)
    region = sa.Column(sa.String, primary_key=True, index=True)
    value = sa.Column(sa.Float)
    core = sa.Column(sa.String, primary_key=True)
    run_id = sa.Column(
        sa.Integer,
        sa.ForeignKey('run.id', onupdate='CASCADE', ondelete='CASCADE'),
        nullable=False,
        primary_key=True)


def persist_likwid(run, session, measurements):
    """
    Persist all likwid results.

    Args:
        run: The run we attach our measurements to.
        session: The db transaction we belong to.
        measurements: The likwid measurements we want to store.
    """
    for (region, name, core, value) in measurements:
        db_measurement = Likwid(
            metric=name, region=region, value=value, core=core, run_id=run.id)
        session.add(db_measurement)


class PJITlikwid(pj.PolyJIT):
    """
    An experiment that uses likwid's instrumentation API for profiling.

    This instruments all projects with likwid instrumentation API calls
    in key regions of the JIT.

    This allows for arbitrary profiling of PolyJIT's overhead and run-time
    """

    NAME = "pj-likwid"
    SCHEMA = [Likwid.__table__]

    def actions_for_project(self, project):
        project = pj.PolyJIT.init_project(project)
        project.cflags = ["-DLIKWID_PERFMON"] + project.cflags

        actns = []
        for i in range(1, int(str(CFG["jobs"])) + 1):
            cp = copy.deepcopy(project)
            cp.run_uuid = uuid.uuid4()
            cp.runtime_extension = \
                RunWithLikwid(
                    cp, self,
                    ext.run.RuntimeExtension(cp, self, config={'jobs': i}),
                    config={'jobs': i})

            actns.append(actions.RequireAll(self.default_runtime_actions(cp)))
        return actns
