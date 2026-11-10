"""Finds the most viral-worthy moments in an episode.

Signals (all free, computed locally):
  * transcript language: controversy, reveals, relationships, money, hot takes, shock
  * famous people mentioned (tiered) + learned boosts from the X study
  * viewer comments that cite a timestamp ("47:12 he really said that") - strongest early signal
  * YouTube "most replayed" heatmap when it exists
  * the episode title / chapters (podcasts tease their hottest moment there)
  * laughter / profanity / question-then-answer structure
Penalties: sponsor reads, intro/outro, music, fragments.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from .text import PeopleIndex, clean, has_laugh, norm, title_keywords

LEX: dict[str, tuple[float, list[str]]] = {
    "controversy": (9.0, [
        "beef", "cheat", "cheated", "cheating", "lied", "lying", "liar", "fake", "snake", "disrespect", "disrespectful",
        "fraud", "scam", "scammed", "exposed", "expose", "lawsuit", "sued", "suing", "arrested", "jail", "prison",
        "fired", "cancelled", "canceled", "blackballed", "set me up", "set up", "betrayed", "backstab", "backstabbed",
        "hate him", "hate her", "can't stand", "fight", "fought", "slapped", "punched", "beat him up", "threatened",
        "diss", "dissed", "shade", "called me out", "called out", "problem with", "owe me", "stole", "robbed", "ratted",
        "snitch", "snitched", "industry plant", "clout", "cap", "capping", "allegations", "allegedly", "accused",
        "blackmail", "kicked out", "banned", "racist", "toxic", "abuse", "manipulat",
    ]),
    "reveal": (8.0, [
        "never told", "never said this", "first time", "the truth", "truth is", "real reason", "secret", "nobody knows",
        "no one knows", "behind the scenes", "what really happened", "what happened was", "i'm gonna tell you",
        "let me tell you", "you won't believe", "confess", "admit", "i admit", "revealed", "reveal", "rumor", "rumors",
        "on record", "for the first time", "the story", "here's the thing", "i found out", "found out", "nobody talks about",
        "they don't want you to know", "off the record", "i regret", "my biggest regret", "the reason why",
    ]),
    "relationships": (7.0, [
        "dating", "girlfriend", "boyfriend", "wife", "husband", "divorce", "divorced", "baby mama", "baby daddy",
        "body count", "onlyfans", "only fans", "hooked up", "slept with", "smash", "smashed", "side chick", "side piece",
        "cheat on", "ex", "my ex", "pregnant", "break up", "broke up", "proposed", "engaged", "married", "situationship",
        "relationship", "simp", "gold digger", "hoe", "hoes", "50/50", "pay for dinner", "high value",
    ]),
    "money": (5.0, [
        "million", "millions", "billion", "broke", "bankrupt", "paid me", "pay me", "contract", "salary", "net worth",
        "rich", "money", "bag", "per show", "offered me", "turned down", "deal", "lawsuit money", "taxes", "irs",
    ]),
    "hot_take": (7.0, [
        "goat", "greatest of all time", "overrated", "underrated", "top five", "top 5", "better than", "worst",
        "trash", "washed", "nobody can", "i'm the best", "best ever", "of all time", "no one is", "fraud", "mid",
        "carried", "would beat", "cooked", "finished", "fell off", "hall of fame", "ranking", "rank",
    ]),
    "shock": (5.0, [
        "oh my god", "what the f", "crazy", "insane", "wild", "are you serious", "no way", "bro what", "hold on",
        "wait wait", "cried", "crying", "scared", "terrified", "shocked", "unbelievable", "speechless", "that's crazy",
        "i'm dead", "deadass", "on god", "swear to god", "for real", "dead serious",
    ]),
}

PROFANITY = ["fuck", "fucking", "shit", "bitch", "ass", "damn", "hell", "motherfucker", "dick", "pussy"]
SPONSOR = ["sponsored", "promo code", "use code", "link in the description", "link in description", "betterhelp",
           "prizepicks", "draftkings", "fanduel", "underdog", "manscaped", "squarespace", "athletic greens", "ag1",
           "our sponsor", "brought to you by", "sign up today", "first deposit", "download the app", "hellofresh",
           "shopify", "chumba", "stake.us", "rocket money", "liquid iv"]
QUESTION_START = re.compile(r"\b(did you|do you|have you|is it true|why did|why do|what happened|how did|was it|"
                            r"would you|who is|who's|what's the|are you)\b")
TS_RX = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?![\d:])")
CONNECTIVE_START = re.compile(r"^(and|but|so|because|cause|like|um|uh|or|then|which|that)\b")


def _compile(words: list[str]) -> list[re.Pattern]:
    out = []
    for w in words:
        w2 = norm(w)
        if w2.endswith(("manipulat",)):
            out.append(re.compile(r"(?<![a-z0-9])" + re.escape(w2)))
        else:
            out.append(re.compile(r"(?<![a-z0-9])" + re.escape(w2) + r"(?![a-z0-9])"))
    return out


_LEX_RX = {k: (w, list(zip(v, _compile(v)))) for k, (w, v) in LEX.items()}
_PROF_RX = _compile(PROFANITY)
_SPON_RX = _compile(SPONSOR)


@dataclass
class Moment:
    start: float
    end: float
    text: str
    score: float
    raw: float
    reasons: list[str] = field(default_factory=list)
    hits: dict = field(default_factory=dict)
    people: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


def comment_timestamps(comments: list[dict], duration: float) -> list[tuple[float, float]]:
    """[(seconds, weight)] from comments like '1:02:15 he really said that'."""
    out = []
    for c in comments or []:
        txt = c.get("text", "")
        likes = float(c.get("likes") or 0)
        seen = set()
        for m in TS_RX.finditer(txt):
            h, mi, s = m.group(1), int(m.group(2)), int(m.group(3))
            if s >= 60 or (h is None and mi >= 600):
                continue
            sec = (int(h) * 3600 if h else 0) + mi * 60 + s
            if sec <= 5 or (duration and sec > duration) or sec in seen:
                continue
            seen.add(sec)
            out.append((float(sec), 1.0 + math.log1p(likes)))
    return out


def _density(points: list[tuple[float, float]], center: float, bw: float = 18.0) -> float:
    return sum(w * math.exp(-((t - center) ** 2) / (2 * bw * bw)) for t, w in points)


def _heat(heatmap: Optional[list], a: float, b: float) -> float:
    if not heatmap:
        return 0.0
    vals = [h.get("value", 0.0) for h in heatmap if h.get("end_time", 0) > a and h.get("start_time", 0) < b]
    return max(vals) if vals else 0.0


def _chapter_at(chapters: Optional[list], t: float) -> str:
    for c in chapters or []:
        if c.get("start_time", 0) <= t < c.get("end_time", 1e12):
            return c.get("title") or ""
    return ""


def build_windows(segs: list[dict], lo: float, hi: float) -> list[tuple[int, int]]:
    """Candidate windows (i, j inclusive) aligned to caption boundaries, ~lo..hi seconds long."""
    wins = []
    n = len(segs)
    targets = sorted({lo, (lo + hi) / 2, hi})
    for i in range(n):
        s0 = segs[i]["start"]
        j = i
        for tgt in targets:
            while j + 1 < n and (segs[j]["start"] + segs[j]["dur"] - s0) < tgt:
                j += 1
            length = segs[j]["start"] + segs[j]["dur"] - s0
            if lo * 0.8 <= length <= hi * 1.35:
                wins.append((i, j))
    return list(dict.fromkeys(wins))


def score_episode(segs: list[dict], info: dict, people: PeopleIndex, show_name: str, *,
                  lo: float = 8, hi: float = 22, hard_max: float = 35, min_gap: float = 75, top_n: int = 5,
                  min_score: float = 45, playbook: Optional[dict] = None) -> list[Moment]:
    playbook = playbook or {}
    pboost: dict = playbook.get("person_boost", {})
    tboost: dict = playbook.get("topic_boost", {})
    segs = [dict(s, text=clean(s["text"]), laugh=has_laugh(s["text"])) for s in segs if s.get("text")]
    segs = [s for s in segs if s["text"] or s["laugh"]]
    if not segs:
        return []
    duration = float(info.get("duration") or (segs[-1]["start"] + segs[-1]["dur"]))
    cts = comment_timestamps(info.get("comments") or [], duration)
    max_den = max([_density(cts, t) for t, _ in cts] or [0.0])
    tkeys = title_keywords(info.get("title") or "", show_name)
    heatmap = info.get("heatmap")
    chapters = info.get("chapters")

    moments: list[Moment] = []
    for i, j in build_windows(segs, lo, hi):
        a = segs[i]["start"]
        b = min(segs[j]["start"] + segs[j]["dur"], a + hard_max)
        text = " ".join(s["text"] for s in segs[i:j + 1]).strip()
        t = norm(text)
        words = t.split()
        if len(words) < 8:
            continue
        raw = 0.0
        reasons: list[str] = []
        hits: dict = {}
        for cat, (w, rxs) in _LEX_RX.items():
            hs = [word for word, rx in rxs if rx.search(t)]
            if hs:
                pts = w * min(len(hs), 3) ** 0.6
                raw += pts
                words_hit = hs[:3]
                hits[cat] = words_hit
                reasons.append(f"{cat.replace('_', ' ').title()}: {', '.join(words_hit)}")
        found = people.find(text, allow_last_name=False)
        pnames = [p.name for p in found]
        if found:
            best = min(p.tier for p in found)
            pts = {1: 10.0, 2: 7.0, 3: 4.0}.get(best, 3.0) + 2.0 * min(len(found) - 1, 3)
            mult = max([float(pboost.get(p.name, 1.0)) for p in found] or [1.0])
            raw += pts * mult
            reasons.append("Mentions " + ", ".join(pnames[:3]))
        if cts:
            den = _density(cts, (a + b) / 2)
            if den > 0.15:
                rel = den / max_den if max_den else 0
                pts = 30.0 * min(1.0, rel) * min(1.0, 0.35 + 0.25 * math.log1p(den))
                raw += pts
                n_near = sum(1 for tt, _ in cts if a - 20 <= tt <= b + 20)
                if n_near:
                    reasons.append(f"Viewers flagged this moment in {n_near} comment{'s' if n_near > 1 else ''}")
        hv = _heat(heatmap, a, b)
        if hv > 0.5:
            raw += 15.0 * (hv - 0.5) / 0.5
            reasons.append("Most replayed part")
        if tkeys:
            ov = tkeys & set(words)
            if ov:
                raw += min(12.0, 5.0 * len(ov))
                reasons.append("Matches the episode headline: " + ", ".join(sorted(ov)[:3]))
        ch = _chapter_at(chapters, a)
        if ch:
            cht = norm(ch)
            if any(rx.search(cht) for _, rxs in _LEX_RX.values() for _w, rx in rxs) or people.find(ch):
                raw += 5.0
                reasons.append(f"Chapter: {ch[:40]}")
        if any(s["laugh"] for s in segs[i:j + 2]):
            raw += 5.0
            reasons.append("Big laugh")
        prof = sum(1 for rx in _PROF_RX if rx.search(t))
        if prof:
            raw += min(4.0, 1.5 * prof)
        if QUESTION_START.search(" ".join(words[:12])):
            raw += 3.0
        for topic, mult in tboost.items():
            if topic in t:
                raw *= max(0.7, min(1.6, float(mult)))
        # penalties
        if any(rx.search(t) for rx in _SPON_RX):
            raw -= 40.0
        if a < 90 or (duration and b > duration - 60):
            raw -= 10.0
        if CONNECTIVE_START.match(t):
            raw -= 3.0
        if raw <= 0:
            continue
        score = 100.0 * (1.0 - math.exp(-raw / 35.0))
        moments.append(Moment(a, b, text, round(score, 1), round(raw, 2), reasons, hits, pnames))

    moments.sort(key=lambda m: (-m.score, m.duration))
    picked: list[Moment] = []
    for m in moments:
        if m.score < min_score:
            break
        if all(abs(m.start - p.start) >= min_gap for p in picked):
            picked.append(_snap(m, segs, hard_max, lo, people))
        if len(picked) >= top_n:
            break
    return picked


def _line_signal(text: str, people: PeopleIndex) -> bool:
    t = norm(text)
    return (any(rx.search(t) for _, rxs in _LEX_RX.values() for _w, rx in rxs) or bool(people.find(text))
            or has_laugh(text) or bool(QUESTION_START.search(t)))


def _snap(m: Moment, segs: list[dict], hard_max: float, lo: float, people: PeopleIndex) -> Moment:
    """Shape the clip: start ON the hook (set-up question or first hot line), never on filler,
    then extend forward to the target length and drop dead lines at the end."""
    inside = [s for s in segs if s["start"] >= m.start - 0.05 and s["start"] < m.end]
    while len(inside) > 1 and not _line_signal(inside[0]["text"], people):
        inside.pop(0)
    if inside:
        first = inside[0]
        for s in segs:  # include the question that sets the moment up, if it is just before
            if first["start"] - 7 <= s["start"] < first["start"] and QUESTION_START.search(norm(s["text"])):
                inside.insert(0, s)
                break
        st = inside[0]["start"]
        idx = segs.index(inside[-1])
        while inside[-1]["start"] + inside[-1]["dur"] - st < lo and idx + 1 < len(segs) \
                and segs[idx + 1]["start"] + segs[idx + 1]["dur"] - st <= hard_max:
            idx += 1
            inside.append(segs[idx])
        while len(inside) > 1 and (inside[-2]["start"] + inside[-2]["dur"] - st) >= lo \
                and not _line_signal(inside[-1]["text"], people):
            inside.pop()
        m.start = st
        m.end = inside[-1]["start"] + inside[-1]["dur"]
        m.text = " ".join(s["text"] for s in inside if s["text"])
    m.start = max(0.0, m.start - 0.4)
    m.end = min(m.end + 0.6, m.start + hard_max)  # never longer than the maximum
    return m


def episode_hotness(podcast_tier: int, guest_tier: int, views: int, hours_old: float,
                    person_boost: float = 1.0) -> float:
    """0-100 rank of how big an episode is likely to be (who + how fast it is moving)."""
    base = {1: 30.0, 2: 20.0, 3: 12.0}.get(podcast_tier, 10.0)
    g = {1: 35.0, 2: 22.0, 3: 12.0}.get(guest_tier, 5.0) * person_boost
    vph = views / max(hours_old, 0.25) if views else 0.0
    vel = min(35.0, 7.0 * math.log10(1 + vph))
    return round(min(100.0, base + g + vel), 1)
