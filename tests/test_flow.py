import json
import random
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fakes  # noqa: E402
from brain import xstudy  # noqa: E402
from brain.config import Config  # noqa: E402
from brain.crypto import decrypt_obj  # noqa: E402
from brain.learn import build_playbook  # noqa: E402
from brain.text import PeopleIndex  # noqa: E402

UTC = timezone.utc
PEOPLE = PeopleIndex(Config().people)


def test_syndication_and_fx_parsers():
    posts = xstudy.parse_syndication(fakes.syndication_html("fearedbuck", 4), "fearedbuck")
    assert len(posts) == 4, "retweet must be skipped"
    p = posts[0]
    assert p["account"] == "fearedbuck" and p["duration"] == 14.0 and p["likes"] > 0 and p["created"].endswith("Z")
    fx = xstudy.parse_fx(fakes.fx_json("123", 2000000))
    assert fx["views"] == 2000000 and fx["has_video"] and fx["account"] == "fearedbuck"
    ap = xstudy.map_apify({"id": "9", "author": {"userName": "abc"}, "text": "x", "viewCount": 5,
                           "createdAt": "Mon Nov 02 10:00:00 +0000 2026", "media": [{"type": "video"}]})
    assert ap["id"] == "9" and ap["account"] == "abc" and ap["views"] == 5 and ap["has_video"]


def test_run_study_collects_and_rechecks():
    cfg = Config()
    cfg.x_accounts = ["fearedbuck", "blockedacct"]
    cfg.x_search_terms = []
    routes = {
        "screen-name/fearedbuck": fakes.FakeResp(200, fakes.syndication_html("fearedbuck", 5, hours_ago=30)),
        "screen-name/blockedacct": fakes.FakeResp(403, "no"),
        "api.fxtwitter.com": lambda url: fakes.FakeResp(200, js=fakes.fx_json(url.rsplit("/", 1)[1], 1_200_000)),
    }
    xs = {}
    h = xstudy.run_study(fakes.FakeHttp(routes), cfg, xs, PEOPLE, cfg.podcasts)
    assert h["new"] == 5 and h["sources"]["blockedacct"] == "blocked" and h["rechecked"] == 5
    p = next(iter(xs["posts"].values()))
    assert p["views"] == 1_200_000 and set(p["checked"]) == {1.0, 6.0, 24.0}
    assert "Katt Williams" in p["people"] and p["podcast"] == "Club Shay Shay"


def _synthetic_posts(n=240, seed=7):
    """Ground truth: Katt Williams clips do 2.5x, emoji captions do 0.5x, account B is 10x bigger."""
    rnd = random.Random(seed)
    posts = {}
    now = datetime.now(UTC)
    for i in range(n):
        acc = "biga" if i % 2 else "smallb"
        base = 1_000_000 if acc == "biga" else 100_000
        katt = i % 5 == 0
        emo = i % 7 == 0
        mult = (2.5 if katt else 1.0) * (0.5 if emo else 1.0) * rnd.lognormvariate(0, 0.35)
        who = "Katt Williams" if katt else rnd.choice(["Drake", "Kevin Hart", "Shannon Sharpe", "Cardi B"])
        created = now - timedelta(hours=30 + i)
        posts[str(i)] = {"id": str(i), "account": acc, "text": f"{who} goes off on podcast" + (" 😂" if emo else ""),
                         "created": created.strftime("%Y-%m-%dT%H:%M:%SZ"), "views": int(base * mult),
                         "people": [who], "words": 6, "emoji": emo, "quote": False, "hour_utc": created.hour,
                         "duration": 14, "snaps": []}
    return posts


def test_playbook_recovers_ground_truth():
    pb = build_playbook(_synthetic_posts())
    assert pb["n_posts"] == 240
    pbst = pb["person_boost"]
    assert pbst["Katt Williams"] > 1.6, pbst
    assert all(pbst[n] < 1.25 for n in ("Drake", "Kevin Hart", "Cardi B"))
    assert any("Emoji in caption: best no emoji" in s for s in pb["insights"]), pb["insights"]
    assert pb["style_examples"]


def test_playbook_waits_for_data():
    pb = build_playbook(dict(list(_synthetic_posts().items())[:5]))
    assert pb["person_boost"] == {} and "Learning" in pb["insights"][0]


def test_full_cycle_offline(tmp_path, monkeypatch):
    """Discover -> analyse -> clips -> batches -> encrypted publish, with YouTube faked."""
    from brain import pipeline, youtube as yt
    from brain.store import APP_FILE, LocalStore

    monkeypatch.setattr(yt, "resolve_channel", lambda http, name, handle="": (
        {"channel_id": "UCabcdefghijklmnopqrstuv", "title": name, "method": "test"} if name == "Club Shay Shay" else None))
    monkeypatch.setattr(yt, "fetch_rss", lambda http, cid: yt.parse_rss(fakes.rss()))
    monkeypatch.setattr(yt, "search", lambda q, n=10, by_date=True: [])
    monkeypatch.setattr(yt, "video_info", lambda vid, with_comments=True: fakes.info(vid))
    monkeypatch.setattr(yt, "transcript", lambda http, vid, info=None: (fakes.transcript(), "test"))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    store = LocalStore(str(tmp_path))
    c = pipeline.Cycle(mode="cloud", store=store, password="test-password-123")
    c.cfg.x_accounts = []
    health = c.run(("watch", "study"))
    app = decrypt_obj((tmp_path / APP_FILE).read_bytes(), "test-password-123")
    assert health["sources_found"] == 1
    assert app["clips"], "no clips produced"
    top = app["clips"][0]
    assert top["video_id"] == "vid00000001" and 1820 <= top["start"] <= 1850
    assert top["guest"] == "Katt Williams" and 8 <= len(top["caption"].split()) <= 12, top["caption"]
    assert top["lines"] and top["hook"]
    eps = {e["id"]: e for e in app["episodes"]}
    assert "short000001" not in eps and "old00000001" not in eps
    assert len(app["batches"]) == 2 and any(b["clips"] for b in app["batches"])
    first = set(app["batches"][0]["clips"]); second = set(app["batches"][1]["clips"])
    assert not first & second, "a clip was put in two batches"
    # second run must not duplicate and should keep state
    c2 = pipeline.Cycle(mode="cloud", store=store, password="test-password-123")
    c2.cfg.x_accounts = []
    c2.run(("watch",))
    app2 = decrypt_obj((tmp_path / APP_FILE).read_bytes(), "test-password-123")
    assert len(app2["clips"]) == len(app["clips"])


def test_cycle_marks_blocked_for_laptop(tmp_path, monkeypatch):
    from brain import pipeline, youtube as yt
    from brain.store import BRAIN_FILE, LocalStore

    monkeypatch.setattr(yt, "resolve_channel", lambda http, name, handle="": (
        {"channel_id": "UCabcdefghijklmnopqrstuv", "title": name, "method": "t"} if name == "Club Shay Shay" else None))
    monkeypatch.setattr(yt, "fetch_rss", lambda http, cid: yt.parse_rss(fakes.rss()))
    monkeypatch.setattr(yt, "search", lambda q, n=10, by_date=True: [])

    def blocked(*a, **k):
        raise yt.Blocked("Sign in to confirm you're not a bot")
    monkeypatch.setattr(yt, "video_info", blocked)
    store = LocalStore(str(tmp_path))
    c = pipeline.Cycle(mode="cloud", store=store, password="test-password-123")
    c.cfg.x_accounts = []
    h = c.run(("watch",))
    assert h["youtube_blocked"] and h["pending_for_laptop"] == 1
    brain = decrypt_obj((tmp_path / BRAIN_FILE).read_bytes(), "test-password-123")
    assert brain["episodes"]["vid00000001"]["stage"] == "needs_local"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_cutter_renders_16x9_with_burned_captions(tmp_path):
    from brain.cutter import ffmpeg_render, make_ass
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=6",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-shortest", "-c:v", "libx264", "-c:a", "aac",
                    str(src)], check=True)
    clip = {"start": 100, "end": 106, "hook": "KATT GOES OFF", "lines": [[0, 2.5, "the truth is"], [2.5, 6, "he lied to everybody"]]}
    assert "Dialogue: 1,0:00:00.00,0:00:03.00,Hook" in make_ass(clip, 3)
    dst = tmp_path / "out" / "clip.mp4"
    dst.parent.mkdir()
    ffmpeg_render(shutil.which("ffmpeg"), str(src), str(dst), clip, 1080, True, 3)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "json", str(dst)], capture_output=True, text=True, check=True)
    st = json.loads(probe.stdout)["streams"][0]
    assert (st["width"], st["height"]) == (1920, 1080)
    assert not list(dst.parent.glob("*.ass")), "temp subtitle file left behind"


def test_github_store_history_less_write(monkeypatch):
    """Data branch is written as one parentless commit, then force-moved (repo never grows)."""
    import requests
    from brain import store as st
    calls = []

    class H:
        def post(self, url, **kw):
            calls.append(("POST", url.rsplit("/repos/me/r", 1)[1], kw.get("json")))
            sha = {"blobs": "b1", "trees": "t1", "commits": "c1"}[url.rsplit("/", 1)[1]]
            return fakes.FakeResp(201, js={"sha": sha})

        def get(self, url, **kw):
            calls.append(("GET", url, None))
            if url.endswith("/git/ref/heads/data"):
                return fakes.FakeResp(200, js={"object": {"sha": "c0"}})
            return fakes.FakeResp(404, "nf")

    patched = []
    monkeypatch.setattr(requests, "patch", lambda url, **kw: patched.append((url, kw["json"])) or fakes.FakeResp(200, js={}))
    g = st.GitHubStore("me/r", "tok")
    g.http = H()
    assert g.head() == "c0"
    assert g.read("app.enc") is None
    g.write({"app.enc": b"x", "brain.enc": b"y"}, "msg")
    commit = next(c for c in calls if c[1] == "/git/commits")
    assert commit[2]["parents"] == [] and commit[2]["tree"] == "t1"
    tree = next(c for c in calls if c[1] == "/git/trees")
    assert {t["path"] for t in tree[2]["tree"]} == {"app.enc", "brain.enc"}
    assert patched == [("https://api.github.com/repos/me/r/git/refs/heads/data", {"sha": "c1", "force": True})]
