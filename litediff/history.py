"""Commit-történet, alapág-felismerés és pull request (összevetés) nézet a LiteDiffhez."""
from __future__ import annotations

import time

from litediff.gitops import run_git

PREFERRED = ("develop", "main", "master")
MAX_CANDIDATES = 80
RECENT_DAYS = 180


def _out(repo: str, *args: str) -> str:
    return run_git(repo, *args, check=False).decode("utf-8", "replace").strip()


def _exists(repo: str, ref: str) -> bool:
    return bool(_out(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}"))


def upstream(repo: str) -> str:
    return _out(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")


def _short_name(name: str) -> str:
    return name.split("/", 1)[1] if "/" in name and name.split("/", 1)[0] in ("origin", "upstream") else name


def _created_from_head(repo: str, branch: str) -> str | None:
    """„Created from HEAD” esetén: melyik ágon álltunk, amikor az ág létrejött (a HEAD reflogjából)."""
    marker = f" to {branch}"
    for line in reversed(_out(repo, "reflog", "show", "--format=%gs", "HEAD").splitlines()):
        if line.startswith("checkout: moving from ") and line.endswith(marker):
            return line[len("checkout: moving from "):-len(marker)] or None
    return None

def detect_base(repo: str, branch: str) -> dict:
    """Az ág alapága: VS Code mentése → reflog → a legközelebbi ág."""
    vscode = _out(repo, "config", "--get", f"branch.{branch}.vscode-merge-base")
    if vscode and _short_name(vscode) != branch and vscode != upstream(repo) and _exists(repo, vscode):
        return {"name": vscode, "source": "VS Code"}

    reflog = _out(repo, "reflog", "show", "--format=%gs", f"refs/heads/{branch}").splitlines()
    for line in reversed(reflog):
        if line.startswith("branch: Created from "):
            src = line[len("branch: Created from "):].strip()
            for prefix in ("refs/remotes/", "refs/heads/"):
                if src.startswith(prefix):
                    src = src[len(prefix):]
            if src == "HEAD":
                src = _created_from_head(repo, branch) or src
            if src not in ("HEAD", branch, upstream(repo)) and _short_name(src) != branch and _exists(repo, src):
                return {"name": src, "source": "reflog"}
            break

    up = upstream(repo)
    raw = _out(repo, "for-each-ref", "--format=%(refname)%00%(refname:short)%00%(committerdate:unix)",
               "refs/heads", "refs/remotes")
    preferred, recent = [], []
    cutoff = time.time() - RECENT_DAYS * 86400
    for line in raw.splitlines():
        parts = line.split("\0")
        if len(parts) < 3:
            continue
        ref, short, date = parts
        if ref.endswith("/HEAD") or short in (branch, up) or _short_name(short) == branch:
            continue
        if _short_name(short) in PREFERRED:
            preferred.append(short)
        elif date.isdigit() and int(date) >= cutoff:
            recent.append((int(date), short))
    recent.sort(reverse=True)
    names = list(dict.fromkeys(preferred + [s for _, s in recent[:MAX_CANDIDATES]]))

    best, best_rank = None, None
    for name in names:
        count = _out(repo, "rev-list", "--count", f"{name}..HEAD")
        if not count.isdigit():
            continue
        n = int(count)
        is_preferred = _short_name(name) in PREFERRED
        if n == 0 and not is_preferred:
            continue  # ez az ág a mienkből ágazott le (vagy azonos vele)
        rank = (n, 0 if is_preferred else 1, 0 if "/" in name else 1)
        if best_rank is None or rank < best_rank:
            best, best_rank = name, rank
    return {"name": best, "source": "legközelebbi ág" if best else "nem található"}


def merge_base(repo: str, base: str) -> str:
    return _out(repo, "merge-base", base, "HEAD")


def log(repo: str, base: str | None, skip: int = 0, limit: int = 50) -> dict:
    fmt = "%H%x1f%h%x1f%P%x1f%an%x1f%at%x1f%s%x1f%b%x1e"
    # nem _out: a .strip() a \x1e/\x1f elválasztókat is levágná (a Python ezeket szóköznek tekinti)
    raw = run_git(repo, "log", f"--format={fmt}", f"--skip={skip}", "-n", str(limit), "HEAD", check=False).decode("utf-8", "replace")
    own = set(_out(repo, "rev-list", "-n", "5000", f"{base}..HEAD").split()) if base else set()
    unpushed = set(_out(repo, "rev-list", "-n", "5000", "HEAD", "--not", "--remotes").split())
    commits = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        f = record.split("\x1f")
        if len(f) < 7:
            continue
        sha, short, parents, author, ts, subject, body = f[:7]
        parent_list = parents.split()
        commits.append({
            "sha": sha, "short": short, "parents": parent_list, "author": author,
            "time": int(ts) if ts.isdigit() else 0, "subject": subject, "body": body.strip(),
            "own": sha in own, "unpushed": sha in unpushed, "merge": len(parent_list) > 1,
        })
    return {"commits": commits, "more": len(commits) == limit}


STATUS_MAP = {"A": "new", "C": "new", "D": "deleted", "M": "modified", "T": "modified", "R": "renamed", "U": "conflict"}


def parse_name_status(raw: bytes) -> list[dict]:
    parts = raw.decode("utf-8", "replace").split("\0")
    out, i = [], 0
    while i < len(parts):
        code = parts[i]
        i += 1
        if not code:
            continue
        letter = code[0]
        if letter in "RC":
            if i + 1 >= len(parts):
                break
            orig, path = parts[i], parts[i + 1]
            i += 2
        else:
            if i >= len(parts):
                break
            orig, path = None, parts[i]
            i += 1
        kind = STATUS_MAP.get(letter, "modified")
        out.append({"path": path, "status": kind, "orig_path": orig if kind == "renamed" else None})
    return out


def commit_files(repo: str, sha: str, parent: str | None) -> list[dict]:
    if parent:
        raw = run_git(repo, "diff", "--name-status", "-M", "-z", parent, sha)
    else:
        raw = run_git(repo, "diff-tree", "--root", "-r", "-M", "--name-status", "-z", "--no-commit-id", sha)
    files = parse_name_status(raw)
    files.sort(key=lambda c: c["path"].lower())
    return files


def compare_status(repo: str, base_sha: str, untracked: list[str] | None = None) -> list[dict]:
    """A munkakönyvtár (commitolatlanokkal együtt) eltérése a közös őstől – mint egy pull request."""
    files = parse_name_status(run_git(repo, "diff", "--name-status", "-M", "-z", base_sha))
    seen = {f["path"] for f in files}
    if untracked is None:
        raw = run_git(repo, "ls-files", "--others", "--exclude-standard", "-z").decode("utf-8", "replace")
        untracked = [p for p in raw.split("\0") if p]
    for path in untracked:
        if path not in seen:
            files.append({"path": path, "status": "new", "orig_path": None})
    files.sort(key=lambda c: c["path"].lower())
    return files
