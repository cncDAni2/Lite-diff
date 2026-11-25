"""gitops: állapot, tartalom, mentés, ágak."""
import os
from pathlib import Path

from conftest import git, write
from litediff import gitops


def by_path(changes):
    return {c["path"]: c for c in changes}


def test_status_full_detects_all_kinds(repo):
    write(repo, "a.txt", "alma2\n")
    git(repo, "mv", "b b.txt", "c c.txt")
    os.remove(Path(repo) / "dir" / "d.txt")
    write(repo, "új fájl.txt", "új\n")
    write(repo, "staged.txt", "s\n")
    git(repo, "add", "staged.txt")

    st = gitops.status_full(repo)
    changes = by_path(st["changes"])

    assert st["branch"] == "main"
    assert len(st["head"]) == 40
    assert changes["a.txt"]["status"] == "modified"
    assert changes["c c.txt"] == {"path": "c c.txt", "status": "renamed", "orig_path": "b b.txt"}
    assert changes["dir/d.txt"]["status"] == "deleted"
    assert changes["új fájl.txt"]["status"] == "new"
    assert changes["staged.txt"]["status"] == "new"
    assert st["untracked"] == ["új fájl.txt"]


def test_status_full_clean_repo(repo):
    st = gitops.status_full(repo)
    assert st["changes"] == []


def test_status_matches_porcelain_v1(repo):
    write(repo, "a.txt", "x\n")
    write(repo, "n.txt", "n\n")
    assert by_path(gitops.status_dicts(repo)) == by_path(gitops.status_full(repo)["changes"])


def test_original_and_current(repo):
    write(repo, "a.txt", "alma\nkörte\n")
    text, binary = gitops.original(repo, "a.txt", None, "modified")
    assert (text, binary) == ("alma\n", False)
    cur = gitops.current(repo, "a.txt")
    assert cur["text"] == "alma\nkörte\n" and cur["exists"] and not cur["bom"]
    assert gitops.original(repo, "x.txt", None, "new") == ("", False)


def test_original_of_renamed_file_uses_old_path(repo):
    git(repo, "mv", "b b.txt", "c c.txt")
    assert gitops.original(repo, "c c.txt", "b b.txt", "renamed")[0] == "barack\n"


def test_binary_detection(repo):
    (Path(repo) / "kep.bin").write_bytes(b"\x89PNG\x00\x01\x02")
    assert gitops.current(repo, "kep.bin")["binary"] is True


def test_save_keeps_bom_and_line_endings(repo):
    (Path(repo) / "bom.txt").write_bytes(b"\xef\xbb\xbfelso\r\nmasodik\r\n")
    cur = gitops.current(repo, "bom.txt")
    assert cur["bom"] and cur["text"] == "elso\r\nmasodik\r\n"
    gitops.save(repo, "bom.txt", cur["text"] + "harmadik\r\n", cur["bom"])
    assert (Path(repo) / "bom.txt").read_bytes() == b"\xef\xbb\xbfelso\r\nmasodik\r\nharmadik\r\n"


def test_find_repos_in_parent_folder(tmp_path, repo):
    found = gitops.find_repos(str(Path(repo).parent))
    assert found == [str(Path(repo).resolve())]
    assert gitops.find_repos(repo) == [str(Path(repo).resolve())]


def test_branches_and_switch(remote_repo):
    repo = remote_repo
    git(repo, "branch", "feature")
    git(repo, "push", "-q", "origin", "main:csak-tavoli")
    git(repo, "fetch", "-q")
    b = gitops.branches(repo)
    assert b["current"] == "main"
    assert set(b["local"]) == {"main", "feature"}
    assert b["remote"] == ["origin/csak-tavoli"]

    gitops.switch(repo, "feature", False)
    assert gitops.branch(repo) == "feature"
    gitops.switch(repo, "origin/csak-tavoli", True)
    assert gitops.branch(repo) == "csak-tavoli"
