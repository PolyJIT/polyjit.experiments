import glob
import logging
import os

import attr
import sqlalchemy as sa

import benchbuild.experiment as exp
import benchbuild.extensions as ext
import benchbuild.utils.actions as actns
import benchbuild.utils.schema as schema

from benchbuild.utils.cmd import llvm_profdata
from plumbum import local

LOG = logging.getLogger(__name__)


class FileContent(schema.BASE):
    __tablename__ = 'filecontents'

    experience_id = sa.Column(
        schema.GUID,
        sa.ForeignKey("experiment.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        primary_key=True)
    rungroup_id = sa.Column(
        schema.GUID,
        sa.ForeignKey("rungroup.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        primary_key=True)
    filename = sa.Column(sa.String, nullable=False, primary_key=True)
    content = sa.Column(sa.LargeBinary)


def persist_file(f, experiment_id, run_group):
    """
    Persist a file in the FileContent relation.

    Args:
        f (str):
            The filename we want to persist.
        experiment_id (uuid):
            The experiment uuid this file needs to be assigned to.
        run_group (uuid):
            The run group uuid this file needs to be assigned to.
    """
    from benchbuild.utils.schema import Session
    import pathlib
    session = Session()

    filename = os.path.basename(f)
    filepath = pathlib.Path(f)
    session = Session()
    session.add(
        FileContent(
            experience_id=experiment_id,
            rungroup_id=run_group,
            filename=filename,
            content=filepath.read_bytes()))
    session.commit()


def extract_file(filename, outfile, exp_id, run_group):
    """
    Extract a previously stored file from the database.

    Args:
        filename (str):
            The name of the file associated to the content in the database.
        outfile (str):
            The filepath we want to store the content to.
        exp_id (uuid):
            The experiment uuid the file was stored in.
        run_group (uuid):
            The run_group the file was stored in.
    """
    from benchbuild.utils.schema import Session
    import pathlib

    session = Session()
    result = session.query(FileContent.__table__).get((exp_id, run_group, filename))
    if result:
        filepath = pathlib.Path(outfile)
        filepath.write_bytes(result.content)
    else:
        LOG.error("No file found in database.")


@attr.s
class SaveProfile(actns.Step):
    NAME = "SAVEPROFILE"
    DESCRIPTION = "Save a profile in llvm format in the DB"

    filename = attr.ib(default=None)

    @actns.notify_step_begin_end
    def __call__(self):
        from benchbuild.project import Project
        if not isinstance(self.obj, Project):
            raise AttributeError

        obj_builddir = self.obj.builddir
        outfile = os.path.abspath(os.path.join(obj_builddir, self.filename))
        profiles = os.path.abspath(os.path.join(obj_builddir, "raw-profiles"))
        with local.cwd(profiles):
            merge_profdata = llvm_profdata["merge", "-output={}".format(
                outfile)]
            merge_profdata = merge_profdata[glob.glob('default_*.profraw')]
            merge_profdata()

        exp_id = self.obj.experiment.id
        run_group = self.obj.run_uuid

        persist_file(outfile, exp_id, run_group)
        self.status = actns.StepResult.OK


@attr.s
class RetrieveFile(actns.Step):
    NAME = "RETRIEVEFILE"
    DESCRIPTION = "Retrieve a file from the database"

    filename = attr.ib(default=None)
    run_group = attr.ib(default=None)

    @actns.notify_step_begin_end
    def __call__(self):
        from benchbuild.project import Project

        if not isinstance(self.obj, Project):
            raise AttributeError

        obj_builddir = self.obj.builddir
        outfile = os.path.abspath(os.path.join(obj_builddir, self.filename))
        exp_id = self.obj.experiment.id
        extract_file(self.filename, outfile, exp_id, self.run_group)

        self.status = actns.StepResult.OK


class PGO(exp.Experiment):
    """
    Evaluate Luc Forget's implementation of a loop profile tree.

    The experiment compiles every project three times:
        1. Instrument with profile counters.
        2. Without PGO
        3. With PGO

        Execution proceeds as follows:
            INST: Generate & Run a sub-experiment that stores
                  the profiling information in the database.
            NO-PGO: Compile and Run the project (wrapped with time).
            PGO:    Compile and Run the project (using profiling information
                    from the database, INST).
    """
    NAME = "pgo"
    SCHEMA = [FileContent.__table__]

    def actions_for_project(self, project):
        import copy
        import uuid

        no_pgo_project = copy.deepcopy(project)
        no_pgo_project.run_uuid = uuid.uuid4()
        pgo_project = copy.deepcopy(project)
        pgo_project.run_uuid = uuid.uuid4()

        project.cflags += ["-O3", "-fprofile-generate=./raw-profiles"]
        cfg_inst = {"cflags": project.cflags, "name": "inst"}
        project.compiler_extension = \
            ext.RunWithTimeout(
                ext.RunCompiler(project, self, config=cfg_inst))
        project.runtime_extension = \
            ext.RuntimeExtension(project, self, config=cfg_inst)

        # Still activating pgo for clang pgo optimisation
        no_pgo_project.cflags += [
            "-O3", "-fprofile-use=./raw-profiles", "-mllvm", "-polly",
            "-mllvm", "-stats"
        ]
        cfg_no_pgo = {"cflags": no_pgo_project.cflags, "name": "no-pgo"}
        no_pgo_project.compiler_extension = \
            ext.RunWithTimeout(
                ext.ExtractCompileStats(project, self, config=cfg_no_pgo)
            )

        pgo_project.cflags += [
            "-O3", "-fprofile-use=./raw-profiles", "-mllvm", "-polly",
            "-mllvm", "-polly-pgo-enable"
            "-mllvm", "-stats"
        ]
        cfg_pgo = {"cflags": pgo_project.cflags, "name": "pgo"}
        pgo_project.compiler_extension = \
            ext.RunWithTime(ext.RuntimeExtension(project, self),
                            config=cfg_pgo)

        actions = [
            actns.RequireAll(actions=[
                actns.MakeBuildDir(project),
                actns.Prepare(project),
                actns.Download(project),
                actns.Configure(project),
                actns.Build(project),
                actns.Run(project),
                actns.SaveProfile(project, filename='prog.profdata'),
                actns.Clean(project),
            ]),
            actns.RequireAll(actions=[
                actns.MakeBuildDir(no_pgo_project),
                actns.Prepare(no_pgo_project),
                actns.Download(no_pgo_project),
                actns.Configure(no_pgo_project),
                actns.Build(no_pgo_project),
                actns.Run(no_pgo_project),
                actns.Clean(no_pgo_project)
            ]),
            actns.RequireAll(actions=[
                actns.MakeBuildDir(pgo_project),
                actns.Prepare(pgo_project),
                actns.Download(pgo_project),
                actns.Configure(pgo_project),
                actns.RetrieveFile(
                    pgo_project,
                    filename="prog.profdata",
                    run_group=project.run_uuid),
                actns.Build(pgo_project),
                actns.Run(pgo_project),
                actns.Clean(pgo_project)
            ])
        ]
        return actions
