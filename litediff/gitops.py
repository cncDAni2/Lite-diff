"""Git műveletek a LiteDiffhez: állapot, eredeti és jelenlegi tartalom, mentés."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
BINARY_SNIFF = 8000


class GitError(RuntimeError):
    pass


def run_git(repo: str, *args: str, check: bool = True) -> bytes:
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        cwd=repo,
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )
    if check and proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip() or f"git {' '.join(args)} hiba")
    return proc.stdout


def repo_root(path: str) -> str | None:
    try:
        out = run_git(path, "rev-parse", "--show-toplevel").decode("utf-8").strip()
    except (GitError, FileNotFoundError, NotADirectoryError):
        return None
    return str(Path(out).resolve()) if out else None


def find_repos(path: str) -> list[str]:
    """A mappa maga, ha repó; különben a közvetlen almappái közül a repók."""
    root = repo_root(path)
    if root:
        return [root]
    found = []
    try:
        for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if entry.is_dir() and os.path.exists(os.path.join(entry.path, ".git")):
                found.append(str(Path(entry.path).resolve()))
    except OSError:
        pass
    return found


def branch(repo: str) -> str:
    out = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False).decode("utf-8", "replace").strip()
    return out or "?"


def branches(repo: str) -> dict:
    """Helyi ágak, és azok a távoli ágak, amelyeknek még nincs helyi párja."""
    fmt = "%(refname)%00%(refname:short)"
    raw = run_git(repo, "for-each-ref", f"--format={fmt}", "--sort=-committerdate", "refs/heads", "refs/remotes")
    local, remote = [], []
    for line in raw.decode("utf-8", "replace").splitlines():
        parts = line.split("\0")
        if len(parts) < 2:
            continue
        ref, short = parts[0], parts[1]
        if ref.startswith("refs/heads/"):
            local.append(short)
        elif not ref.endswith("/HEAD"):
            remote.append(short)
    local_set = set(local)
    remote = [r for r in remote if r.split("/", 1)[-1] not in local_set]
    return {"current": branch(repo), "local": local, "remote": remote}


def switch(repo: str, name: str, is_remote: bool) -> None:
    if is_remote:
        run_git(repo, "switch", "--track", name)
    else:
        run_git(repo, "switch", name)

@dataclass
class Change:
    path: str          # a jelenlegi útvonal, / elválasztóval
    status: str        # new | modified | deleted | renamed | conflict
    orig_path: str | None = None


def status(repo: str) -> list[Change]:
    raw = run_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    parts = raw.decode("utf-8", "replace").split("\0")
    changes: list[Change] = []
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        orig = None
        if "R" in xy or "C" in xy:
            orig = parts[i] if i < len(parts) else None
            i += 1
        if xy in CONFLICT_CODES:
            kind = "conflict"
        elif xy == "??" or "A" in xy:
            kind = "new"
        elif "D" in xy:
            kind = "deleted"
        elif "R" in xy:
            kind = "renamed"
        elif "C" in xy:
            kind = "new"
        else:
            kind = "modified"
        changes.append(Change(path=path, status=kind, orig_path=orig))
    changes.sort(key=lambda c: c.path.lower())
    return changes


def status_full(repo: str) -> dict:
    """Egyetlen git hívással: ág, HEAD, változások (porcelain v2)."""
    raw = run_git(repo, "status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all")
    parts = raw.decode("utf-8", "replace").split("\0")
    branch_name, head_sha, upstream = "?", "", ""
    changes: list[Change] = []
    untracked: list[str] = []
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if not entry:
            continue
        kind = entry[0]
        if kind == "#":
            if entry.startswith("# branch.head "):
                branch_name = entry[len("# branch.head "):]
                if branch_name == "(detached)":
                    branch_name = "HEAD"
            elif entry.startswith("# branch.oid "):
                head_sha = entry[len("# branch.oid "):]
                if head_sha == "(initial)":
                    head_sha = ""
            elif entry.startswith("# branch.upstream ") or entry.startswith("# branch.ab "):
                # távoli ág és előny/lemaradás: push vagy fetch után ez változik, a HEAD nem
                upstream = (upstream + " " + entry[len("# branch."):]).strip()
            continue
        if kind == "?":
            untracked.append(entry[2:])
            changes.append(Change(path=entry[2:], status="new"))
            continue
        if kind == "!":
            continue
        fields = entry.split(" ")
        xy = fields[1]
        if kind == "1":
            path = entry.split(" ", 8)[8]
            orig = None
        elif kind == "2":
            path = entry.split(" ", 9)[9]
            orig = parts[i] if i < len(parts) else None
            i += 1
        elif kind == "u":
            path = entry.split(" ", 10)[10]
            orig = None
        else:
            continue
        if kind == "u" or xy in CONFLICT_CODES:
            status_kind = "conflict"
        elif "A" in xy:
            status_kind = "new"
        elif "D" in xy:
            status_kind = "deleted"
        elif "R" in xy:
            status_kind = "renamed"
        elif "C" in xy:
            status_kind = "new"
        else:
            status_kind = "modified"
        changes.append(Change(path=path, status=status_kind, orig_path=orig if status_kind == "renamed" else None))
    changes.sort(key=lambda c: c.path.lower())
    return {"branch": branch_name, "head": head_sha, "upstream": upstream,
            "changes": [asdict(c) for c in changes], "untracked": untracked}

def status_dicts(repo: str) -> list[dict]:
    return [asdict(c) for c in status(repo)]


def _decode(data: bytes) -> tuple[str, bool, bool]:
    """(szöveg, bináris-e, volt-e BOM)."""
    if b"\0" in data[:BINARY_SNIFF]:
        return "", True, False
    bom = data.startswith(b"\xef\xbb\xbf")
    if bom:
        data = data[3:]
    try:
        return data.decode("utf-8"), False, bom
    except UnicodeDecodeError:
        return data.decode("cp1250", "replace"), False, bom


def original(repo: str, path: str, orig_path: str | None, kind: str, rev: str = "HEAD") -> tuple[str, bool]:
    if kind == "new":
        return "", False
    return show_text(repo, rev, orig_path or path)


def show_text(repo: str, rev: str, path: str) -> tuple[str, bool]:
    """Egy fájl tartalma egy adott revízióban: (szöveg, bináris-e)."""
    data = run_git(repo, "show", f"{rev}:{path}", check=False)
    text, binary, _ = _decode(data)
    return text, binary


def head(repo: str) -> str:
    return run_git(repo, "rev-parse", "HEAD", check=False).decode("utf-8", "replace").strip()


def current(repo: str, path: str) -> dict:
    full = os.path.join(repo, path)
    if not os.path.isfile(full):
        return {"text": "", "binary": False, "exists": False, "mtime": 0, "bom": False}
    with open(full, "rb") as f:
        data = f.read()
    text, binary, bom = _decode(data)
    return {"text": text, "binary": binary, "exists": True, "mtime": os.path.getmtime(full), "bom": bom}


def save(repo: str, path: str, text: str, bom: bool) -> float:
    full = os.path.join(repo, path)
    data = text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    with open(full, "wb") as f:
        f.write(data)
    return os.path.getmtime(full)


def mtime(repo: str, path: str) -> float:
    try:
        return os.path.getmtime(os.path.join(repo, path))
    except OSError:
        return 0
