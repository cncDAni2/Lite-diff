"""Módosító git műveletek a LiteDiffhez: visszaállítás, commit, push, statisztika."""
from __future__ import annotations

import os
import re
import subprocess
import time

from litediff.gitops import CREATE_NO_WINDOW, GitError, run_git


def _in_index(repo: str, path: str) -> bool:
    out = run_git(repo, "ls-files", "--", path, check=False)
    return bool(out.strip())


def _restore_from_head(repo: str, *paths: str) -> None:
    run_git(repo, "restore", "--source=HEAD", "--staged", "--worktree", "--", *paths)


def _remove(repo: str, path: str) -> None:
    if _in_index(repo, path):
        run_git(repo, "rm", "-f", "--quiet", "--", path)
    else:
        full = os.path.join(repo, path)
        if os.path.isfile(full):
            os.remove(full)


def revert(repo: str, path: str, status: str, orig_path: str | None) -> None:
    """A fájlt visszaállítja a HEAD állapotára (új fájlnál törli)."""
    if status == "new":
        _remove(repo, path)
    elif status == "renamed" and orig_path:
        _restore_from_head(repo, orig_path)
        _remove(repo, path)
    else:
        _restore_from_head(repo, path)


def stats(repo: str) -> dict:
    """+/− sorok a HEAD-hez képest (a nem követett fájlok sorai is), és a pusholandó commitok száma."""
    added = deleted = 0
    out = run_git(repo, "diff", "HEAD", "--numstat", check=False).decode("utf-8", "replace")
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            added += int(parts[0])
            deleted += int(parts[1])
    untracked = run_git(repo, "ls-files", "--others", "--exclude-standard", "-z", check=False).decode("utf-8", "replace")
    for rel in filter(None, untracked.split("\0")):
        try:
            with open(os.path.join(repo, rel), "rb") as f:
                data = f.read(2_000_000)
            if b"\0" not in data[:8000]:
                added += data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)
        except OSError:
            pass
    upstream = run_git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False).decode().strip()
    ahead = behind = None
    if upstream:
        counts = run_git(repo, "rev-list", "--left-right", "--count", "@{u}...HEAD", check=False).decode().split()
        if len(counts) == 2:
            behind, ahead = int(counts[0]), int(counts[1])
    return {"added": added, "deleted": deleted, "upstream": upstream or None, "ahead": ahead, "behind": behind}


def commit(repo: str, message: str) -> str:
    if not message.strip():
        raise GitError("Üres a commit üzenet.")
    run_git(repo, "add", "-A")
    out = run_git(repo, "commit", "-m", message)
    first = out.decode("utf-8", "replace").splitlines()
    return first[0] if first else "Commit kész."


def push(repo: str) -> str:
    upstream = run_git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False).decode().strip()
    if upstream:
        proc_args = ["push"]
    else:
        proc_args = ["push", "-u", "origin", "HEAD"]
    proc = subprocess.run(["git", *proc_args], cwd=repo, capture_output=True, creationflags=CREATE_NO_WINDOW)
    text = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        raise GitError(text or "A push nem sikerült.")
    match = re.search(r"\S+\.\.\S+\s+\S+\s+->\s+\S+", text)
    return match.group(0) if match else "Push kész."


def copy_to_clipboard(text: str) -> None:
    """Unicode szöveg a Windows vágólapra (ctypes, külső program nélkül)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]

    data = text.encode("utf-16-le") + b"\0\0"
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise OSError("A vágólap foglalt.")
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        user32.SetClipboardData(13, handle)  # CF_UNICODETEXT
    finally:
        user32.CloseClipboard()