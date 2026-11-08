"""ClipRush command line.

  python -m brain run [--mode cloud|laptop] [--tasks watch,study]
  python -m brain cut              # laptop: download + cut today's clips
  python -m brain laptop           # laptop: run a cycle, then cut
  python -m brain selftest         # offline checks of every component
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def load_env_file() -> None:
    for p in (Path(__file__).resolve().parent.parent / "laptop" / "cliprush.env", Path.home() / ".cliprush.env"):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"'))


def sync_config() -> None:
    """Laptop uses the config you edit on GitHub (one source of truth); falls back to the local copy."""
    repo, token = os.environ.get("CLIPRUSH_REPO", ""), os.environ.get("CLIPRUSH_GITHUB_TOKEN", "")
    if not repo:
        return
    import requests
    cache = Path(__file__).resolve().parent.parent / "laptop" / "config_cache"
    cache.mkdir(exist_ok=True)
    ok = 0
    for name in ("settings.yaml", "sources.yaml"):
        try:
            h = {"Accept": "application/vnd.github.raw+json"}
            if token:
                h["Authorization"] = f"Bearer {token}"
            r = requests.get(f"https://api.github.com/repos/{repo}/contents/config/{name}", headers=h, timeout=20)
            if r.status_code == 200 and r.content.strip():
                (cache / name).write_bytes(r.content)
                ok += 1
        except requests.RequestException:
            pass
    if ok == 2:
        os.environ["CLIPRUSH_CONFIG"] = str(cache)


def main(argv=None) -> int:
    load_env_file()
    ap = argparse.ArgumentParser(prog="cliprush")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--mode", default=os.environ.get("CLIPRUSH_MODE", "cloud"))
    r.add_argument("--tasks", default="watch,study")
    r.add_argument("--deadline", type=float, default=float(os.environ.get("CLIPRUSH_DEADLINE", 540)))
    sub.add_parser("cut")
    lp = sub.add_parser("laptop")
    lp.add_argument("--no-cut", action="store_true")
    st = sub.add_parser("selftest")
    st.add_argument("--online", action="store_true", help="also test internet sources and keys")
    a = ap.parse_args(argv)

    if a.cmd == "selftest":
        from .selftest import run_selftest
        return run_selftest(online=a.online)
    if a.cmd == "run":
        from .pipeline import Cycle
        h = Cycle(mode=a.mode, deadline_s=a.deadline).run(tuple(t.strip() for t in a.tasks.split(",") if t.strip()))
        print(f"OK - {h.get('sources_found')}/{h.get('sources_total')} sources, blocked={h.get('youtube_blocked')}")
        return 0
    if a.cmd in ("cut", "laptop"):
        sync_config()
    if a.cmd == "cut":
        from .cutter import run_cutter
        made = run_cutter()
        print(f"Saved {len(made)} clips")
        return 0
    if a.cmd == "laptop":
        from .cutter import run_cutter
        from .pipeline import Cycle
        Cycle(mode="laptop", deadline_s=1500).run(("watch", "study"))
        if not a.no_cut:
            print(f"Saved {len(run_cutter())} clips")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
