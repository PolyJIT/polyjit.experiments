"""
The 'polyjit' experiment.

This experiment uses likwid to measure the performance of all binaries
when running with polyjit support enabled.
"""
import copy
import glob
import logging
import os
import uuid
from abc import abstractmethod

import sqlalchemy as sa
import yaml

import benchbuild.extensions as ext
import benchbuild.utils.schema as schema
import polyjit.experiments.papi as papi
from benchbuild.experiment import Experiment
from benchbuild.utils.actions import Any, RequireAll
from benchbuild.utils.dict import ExtensibleDict, extend_as_list
from benchbuild.utils.run import RunInfo
from plumbum import local

LOG = logging.getLogger(__name__)


class PJ_Result(schema.BASE):
    __tablename__ = 'polyjit_result'
    id = sa.Column(sa.Integer, primary_key=True)
    run_id = sa.Column(
        sa.Integer,
        sa.ForeignKey("run.id", onupdate="CASCADE", ondelete="CASCADE"),
        index=True)
    config = sa.Column(sa.String, index=True)
    name = sa.Column(sa.String, index=True)
    value = sa.Column(sa.Float)

    type = sa.Column(sa.String(8))
    __mapper_args__ = {
        'polymorphic_identity': 'program',
        'polymorphic_on': type
    }


class PJ_Result_Region(PJ_Result):
    __tablename__ = 'polyjit_region_result'
    id = sa.Column(
        sa.Integer,
        sa.ForeignKey(
            'polyjit_result.id',
            primary_key=True,
            onupdate="CASCADE",
            ondelete="CASCADE"),
        primary_key=True)
    region_name = sa.Column(sa.String)
    __mapper_args__ = {'polymorphic_identity': 'region'}


def verbosity_to_polyjit_log_level(verbosity: int):
    """Transfers the verbosity level to a useable polyjit format."""
    polyjit_log_levels = {
        0: "off",
        1: "error",
        2: "warn",
        3: "info",
        4: "debug",
        5: "trace",
        6: "trace",
    }
    return polyjit_log_levels[verbosity]


class PolyJITConfig(object):
    """Object that stores the configuraion of the JIT."""
    __config = ExtensibleDict(extend_as_list)

    @property
    def argv(self):
        """Getter for the configuration held by the config object."""
        return PolyJITConfig.__config

    def clear(self):
        PolyJITConfig.__config.clear()

    def value_to_str(self, key):
        """Prints the value of a given key."""
        if key not in self.argv:
            return ""
        value = self.argv[key]
        if isinstance(value, list):
            value = " ".join(value)
        LOG.debug(" %s=%s", key, value)
        return value


class ClearPolyJITConfig(PolyJITConfig, ext.Extension):
    def __call__(self, *args, **kwargs):
        self.clear()
        return self.call_next(*args, **kwargs)


class PolyJITMetrics(ext.Extension):
    def merge_results(self, payload):
        metrics = payload['pj.metrics']
        events = {
            e['region-id']: {
                'event': e['value']
            }
            for e in metrics['events']
        }
        entries = {
            e['region-id']: {
                'entry': e['value']
            }
            for e in metrics['entries']
        }
        regions = {
            e['region-id']: {
                'region': e['region-name']
            }
            for e in metrics['regions']
        }

        merged = events
        for k in events:
            merged[k].update(entries[k])
            merged[k].update(regions[k])

        return merged

    def evaluate(self, run_info: RunInfo):
        payload = run_info.payload
        run_id = run_info.db_run.id
        config = payload['config']
        merged = self.merge_results(payload)

        def yield_in_region(regions, merged_metrics):
            for value in merged_metrics.values():
                if value['region'] in regions:
                    yield value['event']

        def yield_not_in_region(regions, merged_metrics):
            for value in merged_metrics.values():
                if value['region'] not in regions:
                    yield value['event']

        def yield_not_in_region_rw(regions, merged_metrics):
            for value in merged_metrics.values():
                if value['region'] not in regions:
                    yield (value['region'], value['event'])

        def create_results(session,
                           merged_metrics,
                           name,
                           *regions,
                           subset_fn=yield_in_region,
                           aggr_fn=sum):
            cfg = config.get('name', None)
            value = aggr_fn(subset_fn(regions, merged_metrics))
            session.add(
                PJ_Result(config=cfg, name=name, run_id=run_id, value=value))

        def create_rw_results(session,
                              merged_metrics,
                              name,
                              *regions,
                              subset_fn=yield_not_in_region_rw):
            cfg = config.get('name', None)
            for region, value in subset_fn(regions, merged_metrics):
                session.add(
                    PJ_Result_Region(
                        config=cfg,
                        name=name,
                        run_id=run_id,
                        value=value,
                        region_name=region))

        meta_regions = [
            'START', 'CODEGEN', 'CACHE_HIT', 'VARIANTS', 'BLOCKED', 'REQUESTS'
        ]
        session = schema.Session()
        create_results(session, merged, 't_all', 'START')
        create_results(session, merged, 't_codegen', 'CODEGEN')
        create_results(session, merged, 'n_cachehits', 'CACHE_HIT')
        create_results(session, merged, 'n_variants', 'VARIANTS')
        create_results(session, merged, 'n_blocked', 'BLOCKED')
        create_results(session, merged, 'n_requests', 'REQUESTS')
        create_results(
            session,
            merged,
            't_scops',
            *meta_regions,
            subset_fn=yield_not_in_region)
        create_rw_results(
            session,
            merged,
            't_region',
            *meta_regions,
            subset_fn=yield_not_in_region_rw)

        session.commit()

    def payloads(self, name, results):
        valid_results = [e for e in results if e.payload and name in e.payload]
        for result in valid_results:
            yield result

    def __call__(self, *args, **kwargs):
        res = self.call_next(*args, **kwargs)
        for p in self.payloads("pj.metrics", res):
            self.evaluate(p)
        return res


class CollectMetrics(PolyJITConfig, ext.Extension):
    def __init__(self, *extensions, project=None, **kwargs):
        self.project = project
        super(CollectMetrics, self).__init__(*extensions, **kwargs)

    def add_payload(self, stored_file, run_info):
        if not os.path.exists(stored_file):
            LOG.error("Could not find the stored metrics.")
            return

        run_id = run_info.db_run.id

        with open(stored_file, 'r') as yaml_out:
            metrics = yaml.safe_load(yaml_out)

        run_info.add_payload("pj.metrics", metrics)

    def __call__(self, *args, **kwargs):
        builddir = self.project.builddir
        outfile = 'polyjit.{0}.metadata.yml'.format(self.project.name)
        outfile = os.path.join(os.path.abspath(builddir), outfile)
        pjit_args = ["-polli-track-metrics-outfile='{:s}'".format(outfile)]
        with self.argv(PJIT_ARGS=pjit_args):
            res = self.call_next(*args, **kwargs)

        # Attach payload to "last" successful RunInfo.
        valid_runs = [r for r in res if not r.failed]
        if valid_runs:
            self.add_payload(outfile, valid_runs[-1])
        else:
            LOG.error("No valid run results to attach our results to.")
        return res


class CollectScopMetadata(PolyJITConfig, ext.Extension):
    def __init__(self, *extensions, project=None, **kwargs):
        self.project = project
        super(CollectScopMetadata, self).__init__(*extensions, **kwargs)

    def __call__(self, binary_command, *args, **kwargs):
        builddir = self.project.builddir
        outfile = 'polyjit.{0}.metadata.yml'.format(self.project.name)
        outfile = os.path.join(os.path.abspath(builddir), outfile)
        pjit_args = [
            "-polli-track-scop-metadata-outfile='{:s}'".format(outfile)
        ]
        with self.argv(PJIT_ARGS=pjit_args):
            res = self.call_next(binary_command, *args, **kwargs)

        # Load & Parse outfile into the database.
        # TODO
        return res


class EnableJITTracking(PolyJITConfig, ext.Extension):
    """The run and given extensions store polli's statistics to the database."""

    def __init__(self, *args, project=None, **kwargs):
        """Initialize the db object for the JIT."""
        super(EnableJITTracking, self).__init__(
            *args, project=project, **kwargs)
        self.project = project

    def __call__(self, binary_command, *args, **kwargs):
        pjit_args = ["-polli-track"]

        if self.project is None:
            LOG.error("Project was not set."
                      " Database activation will be invalid.")

        with self.argv(PJIT_ARGS=pjit_args):
            return self.call_next(binary_command, *args, **kwargs)


class EnablePolyJIT_Opt(PolyJITConfig, ext.Extension):
    """Call the child extensions with an activated PolyJIT."""

    def __call__(self, *args, **kwargs):
        ret = None
        with self.argv(PJIT_ARGS=["-polli-use-polly-options=false"]):
            with local.env(PJIT_ARGS=self.value_to_str('PJIT_ARGS')):
                ret = self.call_next(*args, **kwargs)
        return ret


class EnablePolyJIT(PolyJITConfig, ext.Extension):
    """Call the child extensions with an activated PolyJIT."""

    def __call__(self, *args, **kwargs):
        ret = None
        with local.env(PJIT_ARGS=self.value_to_str('PJIT_ARGS')):
            ret = self.call_next(*args, **kwargs)
        return ret


class DisableDelinearization(PolyJITConfig, ext.Extension):
    """Deactivate the JIT for the following extensions."""

    def __call__(self, *args, **kwargs):
        ret = None
        with self.argv(PJIT_ARGS=["-polli-no-delinearization"]):
            with local.env(PJIT_ARGS=self.value_to_str('PJIT_ARGS')):
                ret = self.call_next(*args, **kwargs)
        return ret


class DisablePolyJIT(PolyJITConfig, ext.Extension):
    """Deactivate the JIT for the following extensions."""

    def __call__(self, *args, **kwargs):
        ret = None
        with self.argv(PJIT_ARGS=["-polli-no-specialization"]):
            with local.env(PJIT_ARGS=self.value_to_str('PJIT_ARGS')):
                ret = self.call_next(*args, **kwargs)
        return ret


class RegisterPolyJITLogs(PolyJITConfig, ext.LogTrackingMixin, ext.Extension):
    """Extends the following RunWithTime extensions with extra PolyJIT logs."""

    def __call__(self, *args, **kwargs):
        """Redirect to RunWithTime, but register additional logs."""
        from benchbuild.settings import CFG

        log_level = verbosity_to_polyjit_log_level(CFG["verbosity"].value())

        curdir = os.path.realpath(os.path.curdir)
        files_before = glob.glob(os.path.join(curdir, "polyjit.*.log"))

        with self.argv(PJIT_ARGS=[
                "-polli-enable-log", "-polli-log-level={}".format(log_level)
        ]):
            ret = self.call_next(*args, **kwargs)
        files = glob.glob(os.path.join(curdir, "polyjit.*.log"))
        files = [
            new_file for new_file in files if new_file not in files_before
        ]

        for file in files:
            self.add_log(file)

        return ret


class PolyJIT(Experiment):
    """The polyjit experiment."""

    @classmethod
    def init_project(cls, project):
        """
        Execute the benchbuild experiment.

        We perform this experiment in 2 steps:
            1. with likwid disabled.
            2. with likwid enabled.

        Args:
            project: The project we initialize.

        Returns:
            The initialized project.
        """
        project.ldflags += ["-lpjit", "-lgomp"]
        project.cflags = [
            "-fno-omit-frame-pointer", "-rdynamic", "-Xclang", "-load",
            "-Xclang", "LLVMPolly.so", "-Xclang", "-load", "-Xclang",
            "LLVMPolyJIT.so", "-O3", "-mllvm", "-polli-enable-log", "-mllvm",
            "-polli"
        ]
        return project

    @abstractmethod
    def actions_for_project(self, project):
        pass


class PolyJITSimple(PolyJIT):
    """Simple runtime-testing with PolyJIT."""
    NAME = "pj-simple"
    SCHEMA = [papi.Event.__table__]

    def actions_for_project(self, project):
        from benchbuild.settings import CFG

        project = PolyJIT.init_project(project)
        project.run_uuid = uuid.uuid4()
        log_level = verbosity_to_polyjit_log_level(CFG["verbosity"].value())

        project.cflags += [
            "-mllvm", "-polli-log-level={}".format(log_level), "-mllvm",
            "-stats"
        ]

        cfg = {
            "cflags": ' '.join(project.cflags),
            "recompilation": "enabled",
            "specialization": "enabled"
        }

        project.runtime_extension = \
            ext.RuntimeExtension(project, self, config=cfg) \
            << EnablePolyJIT() \
            << EnableJITTracking(project=project) \
            << CollectMetrics(project=project) \
            << PolyJITMetrics() \
            << RegisterPolyJITLogs() \
            << ext.LogAdditionals() \
            << ClearPolyJITConfig() \
            << ext.RunWithTime()

        return PolyJITSimple.default_runtime_actions(project)


class PolyJITFull(PolyJIT):
    """
    An experiment that executes all projects with PolyJIT support.

    This is our default experiment for speedup measurements.
    """

    NAME = "pj"
    SCHEMA = [papi.Event.__table__]

    def actions_for_project(self, project):
        from benchbuild.settings import CFG

        project.cflags = ["-O3", "-fno-omit-frame-pointer"]

        actns = []
        rawp = copy.deepcopy(project)
        rawp.run_uuid = uuid.uuid4()
        rawp.runtime_extension = \
            ext.RuntimeExtension(
                rawp, self, config={"jobs": 1, "name": "Baseline O3"}) \
            << ext.SetThreadLimit(config={"jobs": 1}) \
            << ext.RunWithTime()
        actns.append(RequireAll(self.default_runtime_actions(rawp)))

        pollyp = copy.deepcopy(project)
        pollyp.run_uuid = uuid.uuid4()
        pollyp.cflags = [
            "-Xclang", "-load", "-Xclang", "LLVMPolly.so", "-mllvm", "-polly",
            "-mllvm", "-polly-parallel"
        ]
        pollyp.runtime_extension = \
            ext.RuntimeExtension(
                pollyp, self, config={"jobs": 1, "name": "Polly (Parallel)"}) \
            << ext.SetThreadLimit(config={"jobs": 1}) \
            << ext.RunWithTime()
        actns.append(RequireAll(self.default_runtime_actions(pollyp)))

        jitp = copy.deepcopy(project)
        jitp = PolyJIT.init_project(jitp)
        norecomp = copy.deepcopy(jitp)
        norecomp.cflags += ["-mllvm", "-polli-no-recompilation"]

        for i in range(2, int(str(CFG["jobs"])) + 1):
            cp = copy.deepcopy(norecomp)
            cp.run_uuid = uuid.uuid4()
            cfg = {
                "jobs": i,
                "cores": str(i - 1),
                "cores-config": str(i),
                "recompilation": "disabled",
                "name": "PolyJIT (No Recompilation)"
            }

            cp.runtime_extension = \
                ext.RuntimeExtension(cp, self, config=cfg) \
                << ext.SetThreadLimit(config=cfg) \
                << DisablePolyJIT() \
                << EnableJITTracking(project=cp) \
                << ClearPolyJITConfig() \
                << ext.RunWithTime() \
                << RegisterPolyJITLogs() \
                << ext.LogAdditionals()
            actns.append(RequireAll(self.default_runtime_actions(cp)))

        for i in range(2, int(str(CFG["jobs"])) + 1):
            cp = copy.deepcopy(jitp)
            cp.run_uuid = uuid.uuid4()
            cfg = {
                "jobs": i,
                "cores": str(i - 1),
                "cores-config": str(i),
                "recompilation": "enabled",
                "name": "PolyJIT (Recompilation)"
            }

            cp.runtime_extension = \
                ext.RuntimeExtension(cp, self, config=cfg) \
                << ext.SetThreadLimit(config=cfg) \
                << EnablePolyJIT() \
                << EnableJITTracking(project=cp) \
                << ClearPolyJITConfig() \
                << RegisterPolyJITLogs() \
                << ext.LogAdditionals()
            actns.append(RequireAll(self.default_runtime_actions(cp)))

        return [Any(actions=actns)]
