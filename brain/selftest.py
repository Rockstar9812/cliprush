"""`python -m brain selftest [--online]` - checks every part and says how to fix what fails."""
from __future__ import annotations

import importlib
import os
import sys
import time

OK, BAD, WARN = "PASS", "FAIL", "WARN"


def _line(status: str, name: str, detail: str = "") -> None:
    print(f"  [{status}] {name}" + (f" - {detail}" if detail else ""))


def run_selftest(online: bool = False) -> int:
    fails = 0
    print("\nClipRush self-test\n")
    v = sys.version_info
    _line(OK if v >= (3, 10) else BAD, "Python", f"{v.major}.{v.minor}")
    fails += v < (3, 10)
    for mod in ("yt_dlp", "youtube_transcript_api", "cryptography", "yaml", "requests"):
        try:
            importlib.import_module(mod)
            _line(OK, f"package {mod}")
        except ImportError:
            _line(BAD, f"package {mod}", "run: python -m pip install -r requirements.txt")
            fails += 1
    if fails:
        return 1

    from .config import Config
    from .crypto import decrypt_obj, encrypt_obj
    from .cutter import find_ffmpeg
    from .scoring import score_episode
    from .text import PeopleIndex
    cfg = Config()
    _line(OK if cfg.podcasts else BAD, "config", f"{len(cfg.podcasts)} podcasts, {len(cfg.people)} people, "
          f"{len(cfg.x_accounts)} X accounts")
    ff = find_ffmpeg()
    _line(OK if ff else WARN, "ffmpeg (laptop cutting)", ff or "not found - laptop cannot cut clips; run install_windows.bat")
    pw = os.environ.get("CLIPRUSH_PASSWORD", "")
    _line(OK if len(pw) >= 8 else BAD, "CLIPRUSH_PASSWORD", "set" if len(pw) >= 8 else "missing or under 8 characters")
    fails += len(pw) < 8
    try:
        assert decrypt_obj(encrypt_obj({"a": 1}, "selftest-pass"), "selftest-pass") == {"a": 1}
        _line(OK, "encryption")
    except Exception as e:  # noqa: BLE001
        _line(BAD, "encryption", str(e))
        fails += 1

    segs = [{"start": float(i * 4), "dur": 3.8, "text": "we just talked about the weekend and regular things"} for i in range(900)]
    segs[400] = {"start": 1600.0, "dur": 3.8, "text": "did you ever have a problem with drake"}
    segs[401] = {"start": 1604.0, "dur": 3.8, "text": "the truth is drake lied to me and set me up that's crazy"}
    segs[402] = {"start": 1608.0, "dur": 3.8, "text": "i never told anyone this he owe me a million dollars"}
    ms = score_episode(segs, {"duration": 3600, "title": "Test", "comments": [{"text": "26:45 he said it", "likes": 50}]},
                       PeopleIndex(cfg.people), "Test Show", min_score=30)
    good = bool(ms) and 1590 <= ms[0].start <= 1610
    _line(OK if good else BAD, "moment scoring", f"top moment at {int(ms[0].start)}s score {ms[0].score}" if ms else "none")
    fails += not good

    if online:
        print("\n  Online checks")
        fails += _online(cfg)
    print("\n" + ("All good." if not fails else f"{fails} problem(s) above. Fix them and run the self-test again.") + "\n")
    return 1 if fails else 0


def _online(cfg) -> int:
    import requests

    from . import youtube as yt
    from .util import Http
    http = Http()
    fails = 0
    try:
        for p in cfg.podcasts[:3]:
            res = yt.resolve_channel(http, p.name, p.handle)
            if res:
                items = yt.fetch_rss(http, res["channel_id"])
                _line(OK if items else WARN, "YouTube feeds", f"{p.name}: {len(items)} recent uploads")
                if items:
                    try:
                        segs, src = yt.transcript(http, items[0]["video_id"])
                        _line(OK if segs else WARN, "YouTube transcripts", f"{len(segs)} lines via {src}")
                    except yt.Blocked:
                        _line(WARN, "YouTube transcripts", "blocked on this machine")
                break
        else:
            _line(WARN, "YouTube feeds", "could not resolve the first podcasts - check handles in config/sources.yaml")
    except yt.Blocked as e:
        _line(WARN, "YouTube", f"blocked here ({e})")
    r = http.get("https://api.fxtwitter.com/jack/status/20")
    _line(OK if r is not None and r.status_code == 200 else WARN, "FxTwitter (X stats, no login)",
          "" if r is not None and r.status_code == 200 else "unreachable right now")
    r = http.get(f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{(cfg.x_accounts or ['x'])[0]}")
    _line(OK if r is not None and r.status_code == 200 else WARN, "X embed timeline (no login)",
          "" if r is not None and r.status_code == 200 else f"status {None if r is None else r.status_code}")
    if os.environ.get("GROQ_API_KEY"):
        r = requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"}, timeout=20)
        _line(OK if r.ok else BAD, "Groq key", "" if r.ok else f"{r.status_code}")
        fails += not r.ok
    else:
        _line(WARN, "Groq key", "not set - captions use rules only (optional)")
    if os.environ.get("GEMINI_API_KEY"):
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": os.environ["GEMINI_API_KEY"]}, timeout=20)
        _line(OK if r.ok else BAD, "Gemini key", "" if r.ok else f"{r.status_code}")
        fails += not r.ok
    if os.environ.get("APIFY_TOKEN"):
        r = requests.get("https://api.apify.com/v2/users/me", params={"token": os.environ["APIFY_TOKEN"]}, timeout=20)
        _line(OK if r.ok else BAD, "Apify token", "" if r.ok else f"{r.status_code}")
        fails += not r.ok
    repo, tok = os.environ.get("CLIPRUSH_REPO", ""), os.environ.get("CLIPRUSH_GITHUB_TOKEN", "")
    if repo and tok:
        from .store import GitHubStore
        t0 = time.time()
        head = GitHubStore(repo, tok).head()
        _line(OK, "GitHub data", f"connected ({'data found' if head else 'no data yet - cloud has not run'}, {time.time() - t0:.1f}s)")
    elif repo or tok:
        _line(BAD, "GitHub data", "set both CLIPRUSH_REPO and CLIPRUSH_GITHUB_TOKEN in laptop/cliprush.env")
        fails += 1
    return fails
