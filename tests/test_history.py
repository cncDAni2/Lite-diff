"""history: alapág-felismerés, commitlista, commitfájlok, pull request nézet."""
from conftest import git, write
from litediff import history


def commit(repo, rel, text, msg):
    write(repo, rel, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def make_feature(repo):
    """main ← develop (1 commit) ← feature (2 commit), a developon a leágazás után még egy commit."""
    git(repo, "switch", "-q", "-c", "develop")
    commit(repo, "dev.txt", "dev\n", "develop munka")
    git(repo, "switch", "-q", "-c", "feature", "develop")  # így a reflogba „Created from develop” kerül
    commit(repo, "f1.txt", "f1\n", "feature 1")
    commit(repo, "a.txt", "alma módosítva\n", "feature 2")
    git(repo, "switch", "-q", "develop")
    commit(repo, "later.txt", "később\n", "develop később")
    git(repo, "switch", "-q", "feature")


def test_detect_base_from_reflog(repo):
    make_feature(repo)
    assert history.detect_base(repo, "feature") == {"name": "develop", "source": "reflog"}


def test_detect_base_from_head_reflog_when_created_from_head(repo):
    git(repo, "switch", "-q", "-c", "develop")
    commit(repo, "dev.txt", "dev\n", "develop munka")
    git(repo, "switch", "-q", "-c", "feature")  # reflog: „Created from HEAD”
    commit(repo, "f.txt", "f\n", "feature")
    assert history.detect_base(repo, "feature") == {"name": "develop", "source": "reflog"}


def test_detect_base_prefers_vscode_setting(repo):
    make_feature(repo)
    git(repo, "config", "branch.feature.vscode-merge-base", "main")
    assert history.detect_base(repo, "feature") == {"name": "main", "source": "VS Code"}


def test_detect_base_nearest_branch_without_reflog(repo):
    make_feature(repo)
    git(repo, "reflog", "expire", "--expire=now", "--all")
    res = history.detect_base(repo, "feature")
    assert res["name"] == "develop" and res["source"] == "legközelebbi ág"


def test_detect_base_skips_child_branches(repo):
    make_feature(repo)
    git(repo, "branch", "feature-gyerek")  # a featureből ágazik le: nem lehet az alapja
    git(repo, "reflog", "expire", "--expire=now", "--all")
    assert history.detect_base(repo, "feature")["name"] == "develop"


def test_log_marks_own_and_unpushed(remote_repo):
    repo = remote_repo
    make_feature(repo)
    data = history.log(repo, "develop", 0, 50)
    subjects = [(c["subject"], c["own"]) for c in data["commits"]]
    assert subjects == [("feature 2", True), ("feature 1", True), ("develop munka", False), ("kezdet", False)]
    assert all(c["unpushed"] for c in data["commits"][:3])
    assert data["commits"][-1]["unpushed"] is False  # a kezdet commit fent van az originen
    assert data["more"] is False


def test_log_paging(repo):
    for i in range(5):
        commit(repo, "a.txt", f"{i}\n", f"c{i}")
    first = history.log(repo, None, 0, 3)
    rest = history.log(repo, None, 3, 3)
    assert first["more"] is True and len(first["commits"]) == 3
    assert [c["subject"] for c in rest["commits"]] == ["c1", "c0", "kezdet"]


def test_merge_commit_flag(repo):
    git(repo, "switch", "-q", "-c", "mellek")
    commit(repo, "m.txt", "m\n", "mellékág")
    git(repo, "switch", "-q", "main")
    commit(repo, "x.txt", "x\n", "fő")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge branch 'mellek'", "mellek")
    top = history.log(repo, None, 0, 1)["commits"][0]
    assert top["merge"] is True and len(top["parents"]) == 2


def test_commit_files(repo):
    git(repo, "mv", "b b.txt", "c c.txt")
    write(repo, "a.txt", "változott\n")
    write(repo, "uj.txt", "új\n")
    git(repo, "rm", "-q", "dir/d.txt")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "vegyes")
    top = history.log(repo, None, 0, 1)["commits"][0]
    files = {f["path"]: f for f in history.commit_files(repo, top["sha"], top["parents"][0])}
    assert files["a.txt"]["status"] == "modified"
    assert files["c c.txt"] == {"path": "c c.txt", "status": "renamed", "orig_path": "b b.txt"}
    assert files["dir/d.txt"]["status"] == "deleted"
    assert files["uj.txt"]["status"] == "new"


def test_commit_files_of_root_commit(repo):
    root = history.log(repo, None, 0, 1)["commits"][0]
    files = history.commit_files(repo, root["sha"], None)
    assert {f["path"] for f in files} == {"a.txt", "b b.txt", "dir/d.txt"}
    assert all(f["status"] == "new" for f in files)


def test_compare_status_like_pull_request(repo):
    make_feature(repo)
    write(repo, "commitolatlan.txt", "még nincs commitolva\n")
    write(repo, "f1.txt", "f1 módosítva\n")
    mb = history.merge_base(repo, "develop")
    files = {f["path"]: f["status"] for f in history.compare_status(repo, mb)}
    # a feature commitjai és a commitolatlan változások igen, a develop későbbi commitja nem
    assert files == {"a.txt": "modified", "f1.txt": "new", "commitolatlan.txt": "new"}


def test_parse_name_status():
    raw = b"M\0a.txt\0R100\0regi.txt\0uj.txt\0D\0torolt.txt\0A\0hozzaadott.txt\0"
    assert history.parse_name_status(raw) == [
        {"path": "a.txt", "status": "modified", "orig_path": None},
        {"path": "uj.txt", "status": "renamed", "orig_path": "regi.txt"},
        {"path": "torolt.txt", "status": "deleted", "orig_path": None},
        {"path": "hozzaadott.txt", "status": "new", "orig_path": None},
    ]
