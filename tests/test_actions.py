"""gitops_actions: visszaállítás, statisztika, commit, push; és az Api pull request nézete."""
import os
from pathlib import Path

import pytest

from conftest import git, write
from litediff import gitops, gitops_actions as actions


def changes(repo):
    return {c["path"]: c for c in gitops.status_full(repo)["changes"]}


@pytest.mark.parametrize("setup, path", [
    (lambda r: write(r, "a.txt", "más\n"), "a.txt"),
    (lambda r: os.remove(Path(r) / "a.txt"), "a.txt"),
    (lambda r: write(r, "uj.txt", "új\n"), "uj.txt"),
    (lambda r: (write(r, "hozzaadott.txt", "h\n"), git(r, "add", "hozzaadott.txt")), "hozzaadott.txt"),
    (lambda r: (write(r, "a.txt", "más\n"), git(r, "add", "a.txt")), "a.txt"),
])
def test_revert_restores_clean_state(repo, setup, path):
    setup(repo)
    ch = changes(repo)[path]
    actions.revert(repo, ch["path"], ch["status"], ch["orig_path"])
    assert changes(repo) == {}
    assert (Path(repo) / "a.txt").read_text(encoding="utf-8") == "alma\n"


def test_revert_renamed(repo):
    git(repo, "mv", "b b.txt", "c c.txt")
    ch = changes(repo)["c c.txt"]
    actions.revert(repo, ch["path"], ch["status"], ch["orig_path"])
    assert changes(repo) == {}
    assert (Path(repo) / "b b.txt").exists() and not (Path(repo) / "c c.txt").exists()


def test_stats_counts_untracked_lines(remote_repo):
    repo = remote_repo
    write(repo, "a.txt", "alma\nkörte\n")
    write(repo, "uj.txt", "1\n2\n3\n")
    st = actions.stats(repo)
    assert (st["added"], st["deleted"]) == (4, 0)
    assert st["upstream"] == "origin/main" and st["ahead"] == 0 and st["behind"] == 0


def test_commit_and_push(remote_repo):
    repo = remote_repo
    write(repo, "a.txt", "új tartalom\n")
    write(repo, "uj.txt", "új\n")
    actions.commit(repo, "teszt commit")
    assert changes(repo) == {}
    assert actions.stats(repo)["ahead"] == 1
    actions.push(repo)
    assert actions.stats(repo)["ahead"] == 0


def test_push_new_branch_sets_upstream(remote_repo):
    repo = remote_repo
    git(repo, "switch", "-q", "-c", "uj-ag")
    write(repo, "x.txt", "x\n")
    actions.commit(repo, "x")
    assert actions.stats(repo)["upstream"] is None
    actions.push(repo)
    assert actions.stats(repo)["upstream"] == "origin/uj-ag"


def test_empty_commit_message_rejected(repo):
    write(repo, "a.txt", "x\n")
    with pytest.raises(gitops.GitError):
        actions.commit(repo, "   ")


def test_api_compare_view(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    import importlib
    import litediff.app as app_module
    app_module = importlib.reload(app_module)

    git(repo, "switch", "-q", "-c", "develop")
    git(repo, "switch", "-q", "-c", "feature")
    write(repo, "f.txt", "f\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "feature")
    write(repo, "w.txt", "w\n")

    api = app_module.Api()
    api._cfg["repos"] = [repo]
    assert [c["path"] for c in api.repo(repo)["changes"]] == ["w.txt"]

    info = api.set_compare(repo, True)
    assert info["compare"]["base"] == "develop"
    assert sorted(c["path"] for c in info["changes"]) == ["f.txt", "w.txt"]

    data = api.open_file(repo, "f.txt", None, "new", info["compare"]["merge_base"])
    assert (data["original"], data["modified"]) == ("", "f\n")

    info = api.set_base(repo, "main")
    assert info["compare"]["source"] == "kézi"
    assert api.set_compare(repo, False)["compare"] is None
