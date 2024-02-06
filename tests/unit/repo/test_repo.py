import os
import shutil

import pytest

from dvc.exceptions import OutputDuplicationError
from dvc.repo import NotDvcRepoError, Repo, locked
from dvc_data.hashfile.hash_info import HashInfo


def test_is_dvc_internal(dvc):
    assert dvc.is_dvc_internal(os.path.join("path", "to", ".dvc", "file"))
    assert not dvc.is_dvc_internal(os.path.join("path", "to-non-.dvc", "file"))


@pytest.mark.parametrize(
    "path",
    [
        os.path.join("dir", "subdir", "file"),
        os.path.join("dir", "subdir"),
        "dir",
    ],
)
def test_find_outs_by_path(tmp_dir, dvc, path):
    (stage,) = tmp_dir.dvc_gen({"dir": {"subdir": {"file": "file"}, "other": "other"}})

    outs = dvc.find_outs_by_path(path, strict=False)
    assert len(outs) == 1
    assert outs[0].fs_path == stage.outs[0].fs_path


def test_find_outs_by_path_does_graph_checks(tmp_dir, dvc):
    tmp_dir.dvc_gen("foo", "foo")
    shutil.copyfile("foo.dvc", "foo-2.dvc")

    dvc._reset()
    with pytest.raises(OutputDuplicationError):
        dvc.find_outs_by_path("foo")


@pytest.mark.parametrize(
    "path",
    [os.path.join("dir", "subdir", "file"), os.path.join("dir", "subdir")],
)
def test_used_objs(tmp_dir, dvc, path):
    tmp_dir.dvc_gen({"dir": {"subdir": {"file": "file"}, "other": "other"}})

    expected = {
        HashInfo("md5", "70922d6bf66eb073053a82f77d58c536.dir"),
        HashInfo("md5", "8c7dd922ad47494fc02c388e12c00eac"),
    }

    used = set()
    for _, obj_ids in dvc.used_objs([path]).items():
        used.update(obj_ids)

    assert used == expected


def test_locked(mocker):
    repo = mocker.MagicMock()
    repo._lock_depth = 0
    repo.method = locked(repo.method)

    args = ()
    kwargs = {}
    repo.method(repo, args, kwargs)

    assert repo.method_calls == [
        mocker.call._reset(),
        mocker.call.method(repo, args, kwargs),
        mocker.call._reset(),
    ]


def test_skip_graph_checks(tmp_dir, dvc, mocker, run_copy):
    # See https://github.com/iterative/dvc/issues/2671 for more info
    from dvc.repo.index import Index

    mock_build_graph = mocker.spy(Index.graph, "fget")

    # sanity check
    tmp_dir.gen("foo", "foo text")
    dvc.add("foo")
    run_copy("foo", "bar", name="copy-foo-bar")
    assert mock_build_graph.called

    # check that our hack can be enabled
    mock_build_graph.reset_mock()
    dvc._skip_graph_checks = True
    tmp_dir.gen("baz", "baz text")
    run_copy("baz", "qux", name="copy-baz-qux")
    assert not mock_build_graph.called

    # check that our hack can be disabled
    mock_build_graph.reset_mock()
    dvc._skip_graph_checks = False
    tmp_dir.gen("quux", "quux text")
    run_copy("quux", "quuz", name="copy-quux-quuz")
    assert mock_build_graph.called


def test_branch_config(tmp_dir, scm):
    tmp_dir.scm_gen("foo", "foo", commit="init")

    # sanity check
    with pytest.raises(NotDvcRepoError):
        Repo().close()

    scm.checkout("branch", create_new=True)
    dvc = Repo.init()
    with dvc.config.edit() as conf:
        conf["remote"]["branch"] = {"url": "/some/path"}
    dvc.close()

    scm.add([os.path.join(".dvc", "config")])
    scm.commit("init dvc")
    scm.checkout("master")

    with pytest.raises(NotDvcRepoError):
        Repo(rev="master").close()

    dvc = Repo(rev="branch")
    try:
        assert dvc.config["remote"]["branch"]["url"] == "/some/path"
    finally:
        dvc.close()


def test_dynamic_cache_initialization(tmp_dir, scm):
    dvc = Repo.init()
    with dvc.config.edit() as conf:
        conf["cache"]["ssh"] = "foo"
        conf["remote"]["foo"] = {"url": "remote://bar/baz"}
    dvc.close()

    Repo(str(tmp_dir)).close()


def test_monorepo_relpath(tmp_dir, scm):
    from dvc.repo.destroy import destroy

    tmp_dir.gen({"project_a": {}, "subdir/project_b": {}})

    non_monorepo = Repo.init(tmp_dir)
    assert non_monorepo.subrepo_relpath == ""

    destroy(non_monorepo)

    monorepo_project_a = Repo.init(tmp_dir / "project_a", subdir=True)

    assert monorepo_project_a.subrepo_relpath == "project_a"

    monorepo_project_b = Repo.init(tmp_dir / "subdir" / "project_b", subdir=True)

    assert monorepo_project_b.subrepo_relpath == "subdir/project_b"


def test_virtual_monorepo_relpath(tmp_dir, scm):
    from dvc.fs.git import GitFileSystem
    from dvc.repo.destroy import destroy

    tmp_dir.gen({"project_a": {}, "subdir/project_b": {}})
    scm.commit("initial commit")
    gfs = GitFileSystem(scm=scm, rev="master")

    non_monorepo = Repo.init(tmp_dir)
    non_monorepo.fs = gfs
    non_monorepo.root_dir = "/"

    assert non_monorepo.subrepo_relpath == ""

    destroy(non_monorepo)

    monorepo_project_a = Repo.init(tmp_dir / "project_a", subdir=True)
    monorepo_project_a.fs = gfs
    monorepo_project_a.root_dir = "/project_a"

    assert monorepo_project_a.subrepo_relpath == "project_a"

    monorepo_project_b = Repo.init(tmp_dir / "subdir" / "project_b", subdir=True)
    monorepo_project_b.fs = gfs
    monorepo_project_b.root_dir = "/subdir/project_b"

    assert monorepo_project_b.subrepo_relpath == "subdir/project_b"
