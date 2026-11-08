"""Where ClipRush keeps its data.

GitHubStore writes to a `data` branch as a single history-less commit each time (force-updated),
so the free repository never grows. It only uses GitHub's REST API - no git install needed,
which is what lets the same code run in GitHub's cloud and on your Windows laptop."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Optional

from .crypto import decrypt_obj, encrypt_obj
from .util import Http, log

APP_FILE = "app.enc"
BRAIN_FILE = "brain.enc"
FEEDBACK_FILE = "feedback.json"


class LocalStore:
    def __init__(self, folder: str):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)

    def head(self) -> str:
        p = self.dir / BRAIN_FILE
        return str(p.stat().st_mtime_ns) if p.exists() else ""

    def read(self, name: str, ref: str = "data") -> Optional[bytes]:
        p = self.dir / (name if ref == "data" else f"{ref}-{name}")
        return p.read_bytes() if p.exists() else None

    def write(self, files: dict[str, bytes], message: str = "") -> bool:
        for n, b in files.items():
            tmp = self.dir / (n + ".tmp")
            tmp.write_bytes(b)
            os.replace(tmp, self.dir / n)
        return True


class GitHubStore:
    def __init__(self, repo: str, token: str, branch: str = "data"):
        self.repo, self.branch = repo, branch
        self.http = Http(min_gap=0.1)
        self.h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                  "X-GitHub-Api-Version": "2022-11-28"}
        self.api = f"https://api.github.com/repos/{repo}"

    def head(self) -> str:
        r = self.http.get(f"{self.api}/git/ref/heads/{self.branch}", headers=self.h)
        if r is not None and r.status_code == 200:
            return r.json()["object"]["sha"]
        return ""

    def read(self, name: str, ref: str = "") -> Optional[bytes]:
        h = dict(self.h, Accept="application/vnd.github.raw+json")
        r = self.http.get(f"{self.api}/contents/{name}", headers=h, params={"ref": ref or self.branch})
        if r is None:
            raise RuntimeError("GitHub unreachable")
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise RuntimeError(f"GitHub read {name}: {r.status_code} {r.text[:120]}")
        return r.content

    def write(self, files: dict[str, bytes], message: str = "ClipRush update") -> bool:
        tree = []
        for name, data in files.items():
            r = self.http.post(f"{self.api}/git/blobs", headers=self.h,
                               json={"content": base64.b64encode(data).decode(), "encoding": "base64"})
            if r is None or r.status_code != 201:
                raise RuntimeError(f"blob {name}: {None if r is None else r.text[:160]}")
            tree.append({"path": name, "mode": "100644", "type": "blob", "sha": r.json()["sha"]})
        r = self.http.post(f"{self.api}/git/trees", headers=self.h, json={"tree": tree})
        if r is None or r.status_code != 201:
            raise RuntimeError(f"tree: {None if r is None else r.text[:160]}")
        r = self.http.post(f"{self.api}/git/commits", headers=self.h,
                           json={"message": message, "tree": r.json()["sha"], "parents": []})
        if r is None or r.status_code != 201:
            raise RuntimeError(f"commit: {None if r is None else r.text[:160]}")
        sha = r.json()["sha"]
        import requests
        rr = requests.patch(f"{self.api}/git/refs/heads/{self.branch}", headers=self.h,
                            json={"sha": sha, "force": True}, timeout=30)
        if rr.status_code == 422 or rr.status_code == 404:
            rr = requests.post(f"{self.api}/git/refs", headers=self.h,
                               json={"ref": f"refs/heads/{self.branch}", "sha": sha}, timeout=30)
        if rr.status_code not in (200, 201):
            raise RuntimeError(f"ref update: {rr.status_code} {rr.text[:160]}")
        return True


def make_store() -> LocalStore | GitHubStore:
    repo = os.environ.get("CLIPRUSH_REPO") or os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("CLIPRUSH_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if repo and token and os.environ.get("CLIPRUSH_STORE", "github") == "github":
        return GitHubStore(repo, token)
    folder = os.environ.get("CLIPRUSH_DATA", str(Path(__file__).resolve().parent.parent / "local_data"))
    log.info("Using local data folder %s", folder)
    return LocalStore(folder)


def load(store, password: str) -> tuple[dict, dict]:
    app = brain = None
    b = store.read(BRAIN_FILE)
    if b:
        brain = decrypt_obj(b, password)
    a = store.read(APP_FILE)
    if a:
        app = decrypt_obj(a, password)
    return app or {}, brain or {}


def load_feedback(store) -> dict:
    try:
        b = store.read(FEEDBACK_FILE, ref="feedback")
    except RuntimeError:
        return {}
    if not b:
        return {}
    try:
        return json.loads(b)
    except ValueError:
        return {}


def save(store, app: dict, brain: dict, password: str, message: str) -> None:
    store.write({APP_FILE: encrypt_obj(app, password), BRAIN_FILE: encrypt_obj(brain, password)}, message)


# ------------------------------------------------------------------ merging
def _newer(a: dict, b: dict) -> dict:
    return a if (a.get("updated") or "") >= (b.get("updated") or "") else b


def merge_brain(mine: dict, theirs: dict) -> dict:
    """Combine two brain states (cloud + laptop wrote at the same time). Nothing is lost."""
    out = dict(theirs)
    out.update({k: v for k, v in mine.items() if k not in ("episodes", "clips", "channels", "xstudy", "ai_usage")})
    for key in ("episodes", "clips", "channels"):
        a, b = mine.get(key, {}), theirs.get(key, {})
        out[key] = {k: _newer(a[k], b[k]) if k in a and k in b else a.get(k) or b.get(k) for k in set(a) | set(b)}
    xa, xb = mine.get("xstudy", {}), theirs.get("xstudy", {})
    xs = dict(xb)
    xs.update({k: v for k, v in xa.items() if k != "posts"})
    posts = dict(xb.get("posts", {}))
    for pid, p in xa.get("posts", {}).items():
        if pid in posts:
            q = dict(posts[pid])
            snaps = {tuple(s) for s in q.get("snaps", [])} | {tuple(s) for s in p.get("snaps", [])}
            q["snaps"] = sorted([list(s) for s in snaps])
            q["checked"] = sorted(set(q.get("checked", [])) | set(p.get("checked", [])))
            for k in ("views", "likes", "reposts", "replies"):
                q[k] = max(q.get(k, 0), p.get(k, 0))
            posts[pid] = q
        else:
            posts[pid] = p
    xs["posts"] = posts
    out["xstudy"] = xs
    ua, ub = mine.get("ai_usage", {}), theirs.get("ai_usage", {})
    out["ai_usage"] = ua if ua.get("date", "") > ub.get("date", "") or ua.get("tokens", 0) >= ub.get("tokens", 0) else ub
    return out
