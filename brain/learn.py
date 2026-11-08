"""Turns the X study into a playbook the scorer uses.

Method (same idea that held up on your 1,010-post dataset): who posts a clip explains a lot of
its reach, so every clip is compared with its own account's typical views ("lift"), and every
estimate is shrunk toward neutral until there is enough evidence (no over-reacting to one viral post)."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Optional

from .scoring import LEX
from .text import norm
from .util import hours_since, iso, now_utc
from .xstudy import views_at

SHRINK = 6.0  # posts of evidence needed before a boost counts for half its measured value


def _final_views(p: dict) -> int:
    return int(max([p.get("views", 0)] + [s[1] for s in p.get("snaps", []) if s[1]]))


def _shrunk(lifts: list[float]) -> float:
    if not lifts:
        return 0.0
    n = len(lifts)
    return statistics.fmean(lifts) * n / (n + SHRINK)


def _bucket(v: Optional[float], edges: list[float], labels: list[str]) -> Optional[str]:
    if v is None:
        return None
    for e, lab in zip(edges, labels):
        if v < e:
            return lab
    return labels[-1]


def build_playbook(posts: dict, min_age_h: float = 20.0) -> dict:
    mature = [p for p in posts.values() if hours_since(p.get("created")) >= min_age_h and _final_views(p) > 0]
    pb: dict = {"built": iso(now_utc()), "n_posts": len(mature), "person_boost": {}, "topic_boost": {},
                "style_examples": [], "insights": [], "accounts": {}, "trending_people": []}
    if len(mature) < 15:
        pb["insights"].append(f"Learning: {len(mature)} mature clips so far. Insights start at 15 (usually 1-2 days).")
        return pb

    by_acc: dict[str, list[int]] = defaultdict(list)
    for p in mature:
        by_acc[p.get("account", "").lower()].append(_final_views(p))
    med = {a: statistics.median(v) for a, v in by_acc.items() if len(v) >= 4}
    pb["accounts"] = {a: {"median_views": int(m), "posts": len(by_acc[a])} for a, m in med.items()}

    rows = []
    for p in mature:
        m = med.get(p.get("account", "").lower())
        if not m:
            continue
        rows.append((p, math.log(max(1, _final_views(p)) / max(1.0, m))))
    if len(rows) < 12:
        pb["insights"].append("Need at least 4 clips per account before comparing; collecting.")
        return pb

    def group(key) -> dict[str, list[float]]:
        g: dict[str, list[float]] = defaultdict(list)
        for p, lift in rows:
            ks = key(p)
            for k in ([ks] if isinstance(ks, str) else ks or []):
                if k is not None:
                    g[k].append(lift)
        return g

    # people
    for name, lifts in group(lambda p: p.get("people") or []).items():
        if len(lifts) >= 2:
            pb["person_boost"][name] = round(max(0.7, min(1.8, math.exp(_shrunk(lifts)))), 3)
    # topics (lexicon words that appear in the post caption)
    lex_words = [w for _, (_, ws) in LEX.items() for w in ws]

    def topics(p):
        t = norm(p.get("text", ""))
        return [w for w in lex_words if f" {norm(w)} " in f" {t} "]
    for w, lifts in group(topics).items():
        if len(lifts) >= 3:
            pb["topic_boost"][norm(w)] = round(max(0.7, min(1.6, math.exp(_shrunk(lifts)))), 3)

    def report(title: str, g: dict[str, list[float]], fmt=lambda k: k, min_n: int = 5) -> None:
        items = [(k, _shrunk(v), len(v)) for k, v in g.items() if len(v) >= min_n]
        if len(items) < 2:
            return
        items.sort(key=lambda x: -x[1])
        best, worst = items[0], items[-1]
        pb["insights"].append(f"{title}: best {fmt(best[0])} ({math.exp(best[1]):.2f}x typical, n={best[2]}), "
                              f"worst {fmt(worst[0])} ({math.exp(worst[1]):.2f}x, n={worst[2]})")

    report("Caption length", group(lambda p: _bucket(p.get("words"), [8, 13], ["under 8 words", "8-12 words", "13+ words"])))
    report("Emoji in caption", group(lambda p: "with emoji" if p.get("emoji") else "no emoji"))
    report("Clip length", group(lambda p: _bucket(p.get("duration") or None, [15, 30, 60],
                                                 ["under 15s", "15-30s", "30-60s", "60s+"])))
    report("Posting hour (IST)", group(lambda p: None if p.get("hour_utc") is None else
                                       f"{(p['hour_utc'] * 60 + 330) // 60 % 24:02d}:00"), min_n=6)
    report("Quote in caption", group(lambda p: "has a quote" if p.get("quote") else "no quote"))
    pods = group(lambda p: p.get("podcast") or None)
    report("Podcast", pods, min_n=3)

    top_people = sorted(pb["person_boost"].items(), key=lambda kv: -kv[1])[:5]
    if top_people:
        pb["insights"].append("Over-performing people right now: " +
                              ", ".join(f"{n} ({b:.2f}x)" for n, b in top_people))

    # early-velocity rule of thumb: views at ~1h for clips that ended over 1M
    hits = [views_at(p, 1) for p, _ in rows if _final_views(p) >= 1_000_000]
    hits = [h for h in hits if h]
    if len(hits) >= 4:
        pb["insights"].append(f"1M+ clips typically had ~{int(statistics.median(hits)):,} views after 1 hour.")

    # style examples: real captions of the strongest clips (used as few-shot examples for the AI)
    strong = sorted(rows, key=lambda r: (-(_final_views(r[0]) >= 700_000), -r[1]))
    seen = set()
    for p, lift in strong:
        cap = (p.get("text") or "").strip()
        if 4 <= len(cap.split()) <= 20 and cap not in seen:
            seen.add(cap)
            pb["style_examples"].append(cap)
        if len(pb["style_examples"]) >= 20:
            break

    # trending: people with strong lift in the last 48 hours (searched first on YouTube)
    recent = defaultdict(list)
    for p, lift in rows:
        if hours_since(p.get("created")) <= 48:
            for n in p.get("people") or []:
                recent[n].append(lift)
    pb["trending_people"] = [n for n, ls in sorted(recent.items(), key=lambda kv: -statistics.fmean(kv[1]))
                             if statistics.fmean(ls) > 0.2][:10]
    return pb
