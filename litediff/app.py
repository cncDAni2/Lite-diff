"""LiteDiff – könnyű, VS Code-szerű git diffnéző több repóhoz."""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import webview

from litediff import gitops
from litediff import gitops_actions as actions
from litediff import history

APP_NAME = "LiteDiff"
WEB_DIR = Path(__file__).resolve().parent / "web"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
POLL_SECONDS = 1.5
ICON_FILE = WEB_DIR / "litediff.ico"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CONFIG_FILE)


class Api:
    def __init__(self) -> None:
        self._window: webview.Window | None = None
        self._lock = threading.Lock()
        self._cfg = load_config()
        self._cfg.setdefault("repos", [])
        self._cfg.setdefault("compare", {})
        self._cfg.setdefault("base_override", {})
        self._signatures: dict[str, str] = {}
        self._base_cache: dict[tuple, dict] = {}
        self._watched: tuple[str, str] | None = None
        self._watched_mtime = 0.0

    def _attach(self, window: webview.Window) -> None:
        self._window = window

    def _save(self) -> None:
        save_config(self._cfg)

    # --- repók ---
    def repos(self) -> list[dict]:
        with self._lock:
            repos = list(self._cfg["repos"])
        return [self._repo_info(r) for r in repos]

    def repo(self, repo: str) -> dict:
        return self._repo_info(repo)

    def _compare_enabled(self, repo: str) -> bool:
        with self._lock:
            return bool(self._cfg["compare"].get(repo))

    def _base(self, repo: str, branch: str, head: str) -> dict:
        with self._lock:
            override = self._cfg["base_override"].get(f"{repo}|{branch}")
        if override:
            return {"name": override, "source": "kézi"}
        key = (repo, branch, head)
        if key not in self._base_cache:
            self._base_cache[key] = history.detect_base(repo, branch)
        return self._base_cache[key]

    def _merge_base(self, repo: str, base: str, head: str) -> str:
        key = ('mb', repo, base, head)
        if key not in self._base_cache:
            self._base_cache[key] = history.merge_base(repo, base)
        return self._base_cache[key]

    def _repo_info(self, repo: str) -> dict:
        info = {"path": repo, "name": os.path.basename(repo), "branch": "?", "head": "", "upstream": "",
                "changes": [], "error": None, "compare": None}
        if not os.path.isdir(repo):
            info["error"] = "A mappa nem található."
            return info
        try:
            st = gitops.status_full(repo)
            info["branch"], info["head"], info["changes"] = st["branch"], st["head"], st["changes"]
            info["upstream"] = st.get("upstream", "")
            if self._compare_enabled(repo):
                base = self._base(repo, info["branch"], info["head"])
                mb = self._merge_base(repo, base["name"], info["head"]) if base["name"] else ""
                info["compare"] = {"base": base["name"], "source": base["source"], "merge_base": mb or None}
                if mb:
                    info["changes"] = history.compare_status(repo, mb, st["untracked"])
                else:
                    info["compare"]["error"] = "Nem található alapág vagy közös ős."
        except gitops.GitError as exc:
            info["error"] = str(exc)
        return info

    def add_repo_dialog(self) -> list[str]:
        if not self._window:
            return []
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return []
        added = []
        for found in gitops.find_repos(result[0]):
            with self._lock:
                if found not in self._cfg["repos"]:
                    self._cfg["repos"].append(found)
                    added.append(found)
        if added:
            with self._lock:
                self._save()
        return added

    def remove_repo(self, repo: str) -> None:
        with self._lock:
            if repo in self._cfg["repos"]:
                self._cfg["repos"].remove(repo)
                self._save()
            self._signatures.pop(repo, None)

    def reorder_repos(self, order: list[str]) -> None:
        with self._lock:
            known = [p for p in order if p in self._cfg["repos"]]
            rest = [p for p in self._cfg["repos"] if p not in known]
            self._cfg["repos"] = known + rest
            self._save()

    # --- pull request nézet, alapág ---
    def set_compare(self, repo: str, enabled: bool) -> dict:
        with self._lock:
            if enabled:
                self._cfg["compare"][repo] = True
            else:
                self._cfg["compare"].pop(repo, None)
            self._save()
            self._signatures.pop(repo, None)
        return self._repo_info(repo)

    def set_base(self, repo: str, base: str | None) -> dict:
        branch = gitops.branch(repo)
        key = f"{repo}|{branch}"
        with self._lock:
            if base:
                self._cfg["base_override"][key] = base
            else:
                self._cfg["base_override"].pop(key, None)
            self._save()
            self._signatures.pop(repo, None)
        return self._repo_info(repo)

    # --- commitok ---
    def history(self, repo: str, skip: int = 0, limit: int = 50) -> dict:
        # a háttérfrissítés a már betöltött darabszámot kéri le újra, hogy a „Továbbiak” se vesszen el
        limit = max(1, min(int(limit), 1000))
        try:
            branch = gitops.branch(repo)
            base = self._base(repo, branch, gitops.head(repo))
            data = history.log(repo, base["name"], skip, limit)
        except gitops.GitError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "repo": repo, "branch": branch, "base": base["name"], "base_source": base["source"], **data}

    def commit_files(self, repo: str, sha: str, parent: str | None) -> dict:
        try:
            return {"ok": True, "files": history.commit_files(repo, sha, parent)}
        except gitops.GitError as exc:
            return {"ok": False, "error": str(exc)}

    def open_commit_file(self, repo: str, sha: str, parent: str | None, path: str,
                         orig_path: str | None, status: str) -> dict:
        orig_text, orig_binary = ("", False)
        if status != "new" and parent:
            orig_text, orig_binary = gitops.show_text(repo, parent, orig_path or path)
        mod_text, mod_binary = ("", False) if status == "deleted" else gitops.show_text(repo, sha, path)
        with self._lock:
            self._watched = None
        return {"original": orig_text, "modified": mod_text, "binary": orig_binary or mod_binary,
                "exists": True, "bom": False, "mtime": 0}

    # --- ágak ---
    def branches(self, repo: str) -> dict:
        try:
            return gitops.branches(repo)
        except gitops.GitError as exc:
            return {"error": str(exc)}

    def switch_branch(self, repo: str, name: str, is_remote: bool) -> dict:
        try:
            gitops.switch(repo, name, is_remote)
        except gitops.GitError as exc:
            return {"ok": False, "error": str(exc)}
        with self._lock:
            self._signatures.pop(repo, None)
        return {"ok": True, "repo": self._repo_info(repo)}

    # --- műveletek (jobbklikk-menük) ---
    def _run(self, repo: str, fn, *args) -> dict:
        try:
            result = fn(*args)
        except (gitops.GitError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        with self._lock:
            self._signatures.pop(repo, None)
        return {"ok": True, "result": result, "repo": self._repo_info(repo)}

    def revert_file(self, repo: str, path: str, status: str, orig_path: str | None) -> dict:
        return self._run(repo, actions.revert, repo, path, status, orig_path)

    def repo_stats(self, repo: str) -> dict:
        try:
            return {"ok": True, **actions.stats(repo)}
        except gitops.GitError as exc:
            return {"ok": False, "error": str(exc)}

    def commit(self, repo: str, message: str, and_push: bool) -> dict:
        res = self._run(repo, actions.commit, repo, message)
        if res["ok"] and and_push:
            pushed = self._run(repo, actions.push, repo)
            if not pushed["ok"]:
                return {"ok": False, "error": "A commit kész, de a push nem sikerült:\n\n" + pushed["error"],
                        "repo": self._repo_info(repo)}
            return pushed
        return res

    def push(self, repo: str) -> dict:
        return self._run(repo, actions.push, repo)

    def copy_path(self, repo: str, path: str) -> dict:
        full = os.path.normpath(os.path.join(repo, path))
        try:
            actions.copy_to_clipboard(full)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": full}

    # --- fájlok ---
    def open_file(self, repo: str, path: str, orig_path: str | None, status: str, base_rev: str | None = None) -> dict:
        orig_text, orig_binary = gitops.original(repo, path, orig_path, status, base_rev or "HEAD")
        cur = gitops.current(repo, path)
        with self._lock:
            self._watched = (repo, path)
            self._watched_mtime = cur["mtime"]
        return {
            "original": orig_text,
            "modified": cur["text"],
            "binary": orig_binary or cur["binary"],
            "exists": cur["exists"],
            "bom": cur["bom"],
            "mtime": cur["mtime"],
        }

    def reload_file(self, repo: str, path: str) -> dict:
        cur = gitops.current(repo, path)
        with self._lock:
            if self._watched == (repo, path):
                self._watched_mtime = cur["mtime"]
        return cur

    def close_file(self) -> None:
        with self._lock:
            self._watched = None

    def save_file(self, repo: str, path: str, text: str, bom: bool) -> dict:
        try:
            mtime = gitops.save(repo, path, text, bom)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        with self._lock:
            if self._watched == (repo, path):
                self._watched_mtime = mtime
        return {"ok": True, "mtime": mtime}

    def get_layout(self) -> dict:
        return self._cfg.get("layout", {})

    def set_layout(self, layout: dict) -> None:
        with self._lock:
            self._cfg["layout"] = layout
            self._save()

    # --- figyelés ---
    def _watch_loop(self) -> None:
        while True:
            time.sleep(POLL_SECONDS)
            try:
                self._poll_once()
            except Exception as exc:  # a figyelő soha ne álljon le
                print("figyelő hiba:", exc, file=sys.stderr)

    def _poll_once(self) -> None:
        if not self._window:
            return
        with self._lock:
            repos = list(self._cfg["repos"])
            watched = self._watched
            watched_mtime = self._watched_mtime
        changed = []
        for repo in repos:
            info = self._repo_info(repo)
            mtimes = [gitops.mtime(repo, c["path"]) for c in info["changes"]]
            signature = json.dumps([info["branch"], info["head"], info["upstream"], info["error"], info["compare"],
                                    info["changes"], mtimes])
            if self._signatures.get(repo) != signature:
                if repo in self._signatures:
                    changed.append(info)
                self._signatures[repo] = signature
        if changed:
            self._window.evaluate_js(f"window.liteDiff && window.liteDiff.onReposChanged({json.dumps(changed)})")
        if watched:
            mtime = gitops.mtime(*watched)
            if mtime != watched_mtime:
                with self._lock:
                    self._watched_mtime = mtime
                self._window.evaluate_js(
                    f"window.liteDiff && window.liteDiff.onFileChanged({json.dumps(watched[0])}, {json.dumps(watched[1])})"
                )


def _set_app_id() -> None:
    """Saját tálcacsoport, hogy a tálcán a LiteDiff ikonja látszódjon, ne a Pythoné."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LiteDiff.App")
    except Exception:
        pass


def _apply_icon(window: webview.Window) -> None:
    try:
        hwnd = ctypes.c_void_p(window.native.Handle.ToInt64())
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        for kind, size in ((0, 24), (1, 48)):  # ICON_SMALL, ICON_BIG
            handle = user32.LoadImageW(None, str(ICON_FILE), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if handle:
                user32.SendMessageW(hwnd, 0x80, ctypes.c_void_p(kind), ctypes.c_void_p(handle))  # WM_SETICON
    except Exception as exc:
        print("ikon hiba:", exc, file=sys.stderr)


def main() -> None:    # kisebb memória: GPU-folyamat nélkül, egyetlen megjelenítő folyamattal
    os.environ.setdefault(
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
        "--disable-gpu --in-process-gpu --renderer-process-limit=1 --disable-background-networking "
        "--disable-features=msSmartScreenProtection,msWebOOUI,msPdfOOUI,SpareRendererForSitePerProcess",
    )
    # a régi kódot ne a gyorsítótárból töltse be a WebView2
    shutil.rmtree(CONFIG_DIR / "webview" / "EBWebView" / "Default" / "Cache", ignore_errors=True)
    _set_app_id()
    api = Api()
    layout = api.get_layout()
    window = webview.create_window(
        APP_NAME,
        url=str(WEB_DIR / "index.html"),
        js_api=api,
        width=layout.get("width", 1500),
        height=layout.get("height", 900),
        min_size=(700, 400),
        maximized=True,
        background_color="#1e1e1e",
        text_select=True,
    )
    api._attach(window)
    window.events.shown += lambda: _apply_icon(window)
    threading.Thread(target=api._watch_loop, daemon=True, name="watcher").start()
    webview.start(gui="edgechromium", debug="--debug" in sys.argv, private_mode=False, http_server=True,
                  storage_path=str(CONFIG_DIR / "webview"))


if __name__ == "__main__":
    main()
