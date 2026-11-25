"""Közös segédek: ideiglenes git repók a tesztekhez."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout.decode("utf-8", "replace")


def write(repo, rel, text):
    path = Path(repo) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


@pytest.fixture
def repo(tmp_path):
    """Egy commitos repó a 'main' ágon: a.txt, b b.txt, dir/d.txt."""
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "teszt@example.com")
    git(path, "config", "user.name", "Teszt Elek")
    git(path, "config", "core.autocrlf", "false")
    write(path, "a.txt", "alma\n")
    write(path, "b b.txt", "barack\n")
    write(path, "dir/d.txt", "dió\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "kezdet")
    return str(path)


@pytest.fixture
def remote_repo(tmp_path, repo):
    """A 'repo' fixture egy csupasz távoli repóval (origin), a main felpusholva."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo
