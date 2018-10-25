"""
Test Maximal Static Expansion.

This tests the maximal static expansion implementation by
Nicholas Bonfante (implemented in LLVM/Polly).
"""
import csv
import logging
import os

import sqlalchemy as sa

from benchbuild import experiment, extensions, reports, settings
from benchbuild.utils import schema
from benchbuild.utils.cmd import time
from polyjit.experiments import compilestats

CFG = settings.CFG
LOG = logging.getLogger(__name__)


def mse_persist_time_and_memory(run, session, timings):
    """
    Persist the memory results in the database.

    Args:
        run: The run we attach this timing results to.
        session: The db transaction we belong to.
        timings: The timing measurements we want to store.
    """

    for timing in timings:
        session.add(schema.Metric(name="time.user_s",
                                  value=timing[0],
                                  run_id=run.id))
        session.add(schema.Metric(name="time.system_s",
                                  value=timing[1],
                                  run_id=run.id))
        session.add(schema.Metric(name="time.real_s",
                                  value=timing[2],
                                  run_id=run.id))
        session.add(schema.Metric(name="time.rss",
                                  value=timing[3],
                                  run_id=run.id))


class MeasureTimeAndMemory(extensions.base.Extension):
    """Wrap a command with time and store the timings in the database."""
    def __call__(self, binary_command, *args, may_wrap=True, **kwargs):
        time_tag = "BENCHBUILD: "
        if may_wrap:
            run_cmd = time["-f", time_tag + "%U-%S-%e-%M", binary_command]

        def handle_timing(run_infos):
            """Takes care of the formating for the timing statistics."""
            session = schema.Session()
            for run_info in run_infos:
                LOG.debug("Persisting time for '%s'", run_info)
                if may_wrap:
                    timings = time.fetch_time_output(
                        time_tag,
                        time_tag + "{:g}-{:g}-{:g}-{:g}",
                        run_info.stderr.split("\n"))
                    if timings:
                        mse_persist_time_and_memory(
                            run_info.db_run, session, timings)
                    else:
                        LOG.warning("No timing information found.")
            session.commit()
            return run_infos

        res = self.call_next(run_cmd, *args, **kwargs)
        return handle_timing(res)


class PollyMSE(experiment.Experiment):
    """The polly experiment."""

    NAME = "polly-mse"

    def actions_for_project(self, project):
        """Compile & Run the experiment with -O3 enabled."""
        project.cflags = [
            "-O3",
            "-fno-omit-frame-pointer",
            "-mllvm", "-polly",
            "-mllvm", "-polly-enable-mse",
            "-mllvm", "-polly-process-unprofitable",
            "-mllvm", "-polly-enable-optree=0",
            "-mllvm", "-polly-enable-delicm=0",
        ]
        project.compiler_extension = \
            extensions.run.WithTimeout(compilestats.ExtractCompileStats(project, self))
        project.runtime_extension = \
            MeasureTimeAndMemory(
                extensions.run.RuntimeExtension(project, self,
                                     config={
                                         'jobs': int(CFG["jobs"].value)}))

        return self.default_runtime_actions(project)


class PollyMSEReport(reports.Report):
    NAME = "polly-mse"
    SUPPORTED_EXPERIMENTS = ["polly-mse"]

    QUERY_EVAL = \
        sa.sql.select([
            sa.column('project_name'),
            sa.column('name'),
            sa.column('bvalue'),
            sa.column('mvalue')
        ]).\
        select_from(
            sa.func.polly_mse_eval(sa.sql.bindparam('exp_ids'))
        )

    def report(self):
        qry = PollyMSEReport.QUERY_EVAL.unique_params(
            exp_ids=self.experiment_ids)
        return self.session.execute(qry).fetchall()

    def generate(self):
        fname = os.path.abspath(self.out_path)
        fname = "{prefix}_mse{ending}".format(
            prefix=os.path.splitext(fname)[0],
            ending=os.path.splitext(fname)[-1])
        res = self.report()
        with open(fname, 'w') as csv_f:
            csv_writer = csv.writer(csv_f)
            csv_writer.writerows([("projct_name", "name", "bvalue", "mvalue")])
            csv_writer.writerows(res)
