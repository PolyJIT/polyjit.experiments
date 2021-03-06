"""
A test experiment for PolyJIT.

This experiment should only be used to test various features of PolyJIT. It
provides only 1 configuration (maximum number of cores) and tests 2 run-time
execution profiles of PolyJIT:
  1) PolyJIT enabled, with specialization
  2) PolyJIT enabled, without specialization
"""
import csv
import logging
import os
import uuid

from benchbuild import extensions, reports, settings
from polyjit.experiments import papi, polyjit

CFG = settings.CFG
LOG = logging.getLogger(__name__)

class Test(polyjit.PolyJIT):
    """
    An experiment that executes all projects with PolyJIT support.

    This is our default experiment for speedup measurements.
    """

    NAME = "pj-test"

    def actions_for_project(self, project):
        project = polyjit.PolyJIT.init_project(project)
        project.run_uuid = uuid.uuid4()
        jobs = int(settings.CFG["jobs"].value)
        project.cflags += [
            "-mllvm", "-stats", "-mllvm", "-polly-num-threads={0}".format(jobs)
        ]

        cfg_with_jit = {
            'jobs': jobs,
            "cflags": project.cflags,
            "cores": str(jobs - 1),
            "cores-config": str(jobs),
            "recompilation": "enabled",
            "specialization": "enabled"
        }

        cfg_without_jit = {
            'jobs': jobs,
            "cflags": project.cflags,
            "cores": str(jobs - 1),
            "cores-config": str(jobs),
            "recompilation": "enabled",
            "specialization": "disabled"
        }

        pjit_extension = extensions.Extension(
                extensions.run.RuntimeExtension(project, self, config=cfg_with_jit) \
                << polyjit.EnablePolyJIT() \
                << polyjit.EnableJITTracking(project=project) \
                << polyjit.RegisterPolyJITLogs() \
                << extensions.log.LogAdditionals() \
                << polyjit.ClearPolyJITConfig(),
                extensions.run.RuntimeExtension(project, self, config=cfg_without_jit) \
                << polyjit.DisablePolyJIT() \
                << polyjit.EnableJITTracking(project=project) \
                << polyjit.RegisterPolyJITLogs() \
                << extensions.log.LogAdditionals() \
                << polyjit.ClearPolyJITConfig()
        )

        project.runtime_extension = extensions.time.RunWithTime(pjit_extension)
        return Test.default_runtime_actions(project)


class EnableDBExport(polyjit.PolyJITConfig):
    """Call the child extensions with an activated PolyJIT."""

    def __call__(self, binary_command, *args, **kwargs):
        ret = None
        with self.argv(
                PJIT_ARGS=["-polli-optimizer=debug", "-polli-database-export"
                           ]):
            ret = self.call_next(binary_command, *args, **kwargs)
        return ret


class JitExportGeneratedCode(polyjit.PolyJIT):
    """
    An experiment that executes all projects with PolyJIT support.

    This is our default experiment for speedup measurements.
    """

    NAME = "pj-db-export"
    SCHEMA = [papi.Event.__table__]

    def actions_for_project(self, project):
        project = polyjit.PolyJIT.init_project(project)
        project.run_uuid = uuid.uuid4()
        jobs = int(settings.CFG["jobs"].value)
        project.cflags += [
            "-Rpass-missed=polli*", "-mllvm", "-stats", "-mllvm",
            "-polly-num-threads={0}".format(jobs)
        ]

        cfg_with_jit = {
            'jobs': jobs,
            "cflags": project.cflags,
            "cores": str(jobs - 1),
            "cores-config": str(jobs),
            "recompilation": "enabled",
            "specialization": "enabled"
        }

        cfg_without_jit = {
            'jobs': jobs,
            "cflags": project.cflags,
            "cores": str(jobs - 1),
            "cores-config": str(jobs),
            "recompilation": "enabled",
            "specialization": "disabled"
        }

        jit = extensions.run.RuntimeExtension(project, self, config=cfg_with_jit)
        enable_jit = polyjit.EnablePolyJIT(jit)

        no_jit = extensions.run.RuntimeExtension(project, self, config=cfg_without_jit)
        disable_jit = polyjit.EnablePolyJIT(no_jit)

        pjit_extension = extensions.Extension(
            EnableDBExport(enable_jit) \
            << polyjit.EnableJITTracking(project=project) \
            << polyjit.RegisterPolyJITLogs() \
            << extensions.log.LogAdditionals() \
            << polyjit.ClearPolyJITConfig(),
            EnableDBExport(disable_jit) \
            << polyjit.EnableJITTracking(project=project) \
            << polyjit.RegisterPolyJITLogs() \
            << extensions.log.LogAdditionals() \
            << polyjit.ClearPolyJITConfig())

        project.runtime_extension = extensions.time.RunWithTime(pjit_extension)
        return JitExportGeneratedCode.default_runtime_actions(project)


class TestReport(reports.Report):
    """
    Writes report to the database.
    """
    import sqlalchemy as sa
    SUPPORTED_EXPERIMENTS = ['pj-test']
    SCHEMA = [papi.Event.__table__]

    QUERY_TOTAL = \
        sa.sql.select([
            sa.column('project'),
            sa.column('domain'),
            sa.column('speedup'),
            sa.column('ohcov_0'),
            sa.column('ohcov_1'),
            sa.column('dyncov_0'),
            sa.column('dyncov_1'),
            sa.column('cachehits_0'),
            sa.column('cachehits_1'),
            sa.column('variants_0'),
            sa.column('variants_1'),
            sa.column('codegen_0'),
            sa.column('codegen_1'),
            sa.column('scops_0'),
            sa.column('scops_1'),
            sa.column('t_0'),
            sa.column('o_0'),
            sa.column('t_1'),
            sa.column('o_1')
        ]).\
        select_from(
            sa.func.pj_test_eval(sa.sql.bindparam('exp_ids'))
        )

    QUERY_REGION = \
        sa.sql.select([
            sa.column('project'),
            sa.column('region'),
            sa.column('cores'),
            sa.column('t_polly'),
            sa.column('t_polyjit'),
            sa.column('speedup')
        ]).\
        select_from(
            sa.func.pj_test_region_wise(sa.sql.bindparam('exp_ids'))
        )

    def report(self):
        print("I found the following matching experiment ids")
        print("  \n".join([str(x) for x in self.experiment_ids]))

        qry = TestReport.QUERY_TOTAL.unique_params(exp_ids=self.experiment_ids)
        yield ("complete",
               ('project', 'domain', 'speedup', 'ohcov_0', 'ocov_1',
                'dyncov_0', 'dyncov_1', 'cachehits_0', 'cachehits_1',
                'variants_0', 'variants_1', 'codegen_0', 'codegen_1',
                'scops_0', 'scops_1', 't_0', 'o_0', 't_1', 'o_1'),
               self.session.execute(qry).fetchall())
        qry = TestReport.QUERY_REGION.unique_params(
            exp_ids=self.experiment_ids)
        yield ("regions", ('project', 'region', 'cores',
                           'T_Polly', 'T_PolyJIT', 'speedup'),
               self.session.execute(qry).fetchall())

    def generate(self):
        for name, header, data in self.report():
            fname = os.path.basename(self.out_path)

            fname = "{prefix}_{name}{ending}".format(
                prefix=os.path.splitext(fname)[0],
                ending=os.path.splitext(fname)[-1],
                name=name)
            with open(fname, 'w') as csv_out:
                print("Writing '{0}'".format(csv_out.name))
                csv_writer = csv.writer(csv_out)
                csv_writer.writerows([header])
                csv_writer.writerows(data)
