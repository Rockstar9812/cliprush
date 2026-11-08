import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fakes  # noqa: E402
from brain import youtube as yt  # noqa: E402
from brain.captions import caption_ok, clean_caption, template_caption  # noqa: E402
from brain.config import Config  # noqa: E402
from brain.crypto import decrypt_obj, encrypt_obj  # noqa: E402
from brain.scoring import comment_timestamps, episode_hotness, score_episode  # noqa: E402
from brain.text import PeopleIndex, guest_from_title  # noqa: E402

PEOPLE = PeopleIndex(Config().people)


def test_config_loads():
    c = Config()
    assert len(c.podcasts) >= 40
    assert any("Porter" in p.name for p in c.podcasts)
    assert "ragnaroknation" in c.x_accounts
    assert c.get("clips.per_episode") == 5


def test_rss_parse_filters():
    items = yt.parse_rss(fakes.rss())
    assert {i["video_id"] for i in items} == {"vid00000001", "short000001", "old00000001"}
    main = next(i for i in items if i["video_id"] == "vid00000001")
    assert main["views"] == 250000 and not main["is_short"]
    assert next(i for i in items if i["video_id"] == "short000001")["is_short"]


def test_guest_from_title():
    assert guest_from_title("Katt Williams | Club Shay Shay", "Club Shay Shay", PEOPLE)[0] == "Katt Williams"
    g, tier = guest_from_title("Michael Porter Jr. On Fame, Faith & The Nuggets | Curious Mike", "Curious Mike", PEOPLE)
    assert g == "Michael Porter Jr" and tier == 1
    g, tier = guest_from_title("Jaylen Waddle Opens Up On Everything | Bussin", "Bussin' With The Boys", PEOPLE)
    assert g == "Jaylen Waddle" and tier == 9


def test_comment_timestamps():
    ts = comment_timestamps(fakes.info()["comments"], 3600)
    secs = sorted(t for t, _ in ts)
    assert secs == [1835.0, 1838.0, 1840.0, 2705.0]


def test_scoring_finds_viral_moment_and_skips_sponsor():
    ms = score_episode(fakes.transcript(), fakes.info(), PEOPLE, "Club Shay Shay", top_n=5, min_score=30)
    assert ms, "no moments"
    top = ms[0]
    assert 1820 <= top.start <= 1850, (top.start, top.text)
    assert "kevin hart" in top.text.lower()
    assert any("comment" in r for r in top.reasons)
    assert top.score >= 70
    assert all(not (595 <= m.start <= 615) for m in ms), "sponsor read was picked"
    assert any(2690 <= m.start <= 2715 for m in ms), "second moment missing"
    for m in ms:
        assert m.duration <= 36


def test_scoring_without_comments_still_ranks_moment():
    inf = fakes.info()
    inf["comments"] = []
    ms = score_episode(fakes.transcript(), inf, PEOPLE, "Club Shay Shay", min_score=30)
    assert ms and 1820 <= ms[0].start <= 1850


def test_playbook_boost_changes_ranking():
    inf = fakes.info()
    inf["comments"] = []
    base = score_episode(fakes.transcript(), inf, PEOPLE, "Club Shay Shay", min_score=0)
    boosted = score_episode(fakes.transcript(), inf, PEOPLE, "Club Shay Shay", min_score=0,
                            playbook={"person_boost": {"LeBron James": 1.8}})
    lb = lambda ms: next(m.score for m in ms if 2690 <= m.start <= 2715)  # noqa: E731
    assert lb(boosted) > lb(base)


def test_hotness_orders_sensibly():
    big = episode_hotness(1, 1, 400000, 3)
    small = episode_hotness(3, 9, 2000, 3)
    assert big > small and 0 <= small <= 100 and big <= 100


def test_captions_rules():
    cap = template_caption("Katt Williams", {"controversy": ["lied"]}, ["Katt Williams", "Kevin Hart"],
                           "man the truth is kevin hart is an industry plant and i never told anyone this")
    assert caption_ok(cap), cap
    assert cap.startswith(("Katt Williams calls out Kevin Hart", "Katt Williams on Kevin Hart")), cap
    assert cap.count('"') % 2 == 0, cap
    assert 8 <= len(cap.split()) <= 12
    assert clean_caption("Wow 😂🔥 this is crazy #viral #fyp") == "Wow this is crazy"


def test_crypto_roundtrip_and_browser_compat(tmp_path):
    obj = {"clips": [{"id": "a", "caption": "Katt Williams “quote” ✓"}], "n": 3}
    blob = encrypt_obj(obj, "correct horse battery")
    assert decrypt_obj(blob, "correct horse battery") == obj
    with pytest.raises(Exception):
        decrypt_obj(blob, "wrong password!!")
    if not shutil.which("node"):
        pytest.skip("node not installed")
    (tmp_path / "blob.json").write_bytes(blob)
    js = ROOT / "tests" / "decrypt_check.mjs"
    out = subprocess.run(["node", str(js), str(tmp_path / "blob.json"), "correct horse battery"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == obj


def test_ai_parse_and_judge():
    from brain.ai import AI, _parse

    assert _parse('noise {"moments":[{"id":"x","viral":9,"standalone":8,"caption":"A b c d e f g h","hook":"WOW"}]}')["x"]["viral"] == 9

    class C:
        def get(self, k, d=None):
            return {"ai.order": ["groq", "gemini"], "ai.daily_token_budget": 100000, "ai.groq_model": "m1",
                    "ai.groq_fallback_model": "m2"}.get(k, d)
    reply = {"choices": [{"message": {"content": json.dumps({"moments": [
        {"id": "c1", "viral": 8.5, "standalone": 9, "why": "beef", "caption": "Katt Williams says Kevin Hart is an industry plant", "hook": "KATT GOES OFF"}]})}}]}
    http = fakes.FakeHttp({"api.groq.com": fakes.FakeResp(200, js=reply)})
    os.environ["GROQ_API_KEY"] = "test"
    try:
        ai = AI(http, C(), {})
        v = ai.judge({"show": "S", "title": "T"}, [{"id": "c1", "text": "quote"}], ["ex"])
    finally:
        os.environ.pop("GROQ_API_KEY")
    assert v["c1"]["viral"] == 8.5 and ai.usage["tokens"] > 0


def test_ai_quota_exhaustion_falls_back():
    from brain.ai import AI

    class C:
        def get(self, k, d=None):
            return {"ai.order": ["groq"], "ai.daily_token_budget": 100000, "ai.groq_model": "m1"}.get(k, d)
    http = fakes.FakeHttp({"api.groq.com": fakes.FakeResp(429, "Rate limit reached ... tokens per day (TPD)")})
    os.environ["GROQ_API_KEY"] = "test"
    try:
        ai = AI(http, C(), {})
        assert ai.judge({"show": "S"}, [{"id": "c1", "text": "q"}], []) == {}
        assert "groq" in ai.usage.get("exhausted", []) and not ai.available
    finally:
        os.environ.pop("GROQ_API_KEY")


def test_merge_brain_keeps_both_sides():
    from brain.store import merge_brain
    a = {"episodes": {"e1": {"stage": "done", "updated": "2026-11-08T10:00:00Z"}},
         "xstudy": {"posts": {"p1": {"snaps": [["t1", 5, 0, 0, 0]], "checked": [1], "views": 5}}}}
    b = {"episodes": {"e1": {"stage": "new", "updated": "2026-11-08T09:00:00Z"}, "e2": {"stage": "new", "updated": "x"}},
         "xstudy": {"posts": {"p1": {"snaps": [["t2", 9, 0, 0, 0]], "checked": [6], "views": 9}, "p2": {}}}}
    m = merge_brain(a, b)
    assert m["episodes"]["e1"]["stage"] == "done" and "e2" in m["episodes"]
    assert m["xstudy"]["posts"]["p1"]["checked"] == [1, 6] and len(m["xstudy"]["posts"]["p1"]["snaps"]) == 2
    assert m["xstudy"]["posts"]["p1"]["views"] == 9 and "p2" in m["xstudy"]["posts"]
