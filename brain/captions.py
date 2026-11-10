"""Caption rules learned from your own 1,010-post dataset:
8-12 words, no emoji (emoji hurt reach badly), name the person, third person."""
from __future__ import annotations

import re
from typing import Optional

EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF‍️]+")
HASHTAG = re.compile(r"(?:^|\s)#\w+")
FILLER_START = re.compile(r"^(?:and|but|so|like|um|uh|yeah|you know|i mean|okay|ok|well|look|bro|man)\b[ ,]*", re.I)

VERB = {
    "controversy": "calls out",
    "reveal": "reveals",
    "relationships": "opens up about",
    "money": "reveals the money behind",
    "hot_take": "says",
    "shock": "reacts to",
}


def clean_caption(text: str, max_words: int = 12) -> str:
    t = EMOJI.sub("", text or "")
    t = HASHTAG.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > 2 and t[0] == t[-1] == '"':
        t = t[1:-1].strip()
    words = t.split()
    if len(words) > max_words:
        t = " ".join(words[:max_words]).rstrip(",;:-")
        if t.count('"') % 2 == 1:
            t += '"'
    return t


def caption_ok(text: str) -> bool:
    n = len((text or "").split())
    return 6 <= n <= 13 and not EMOJI.search(text) and "#" not in text


QUESTION = re.compile(r"^(?:did|do|does|have|has|is|are|was|were|why|what|how|who|would|could|can|will)\b", re.I)
CAT_ORDER = ["controversy", "reveal", "relationships", "hot_take", "money", "shock"]


def _quote_fragment(text: str, n_words: int, hits: dict, lines: list | None = None) -> str:
    """Pick the strongest non-question line and cut a short fragment around its key phrase."""
    pieces = [ln[2] if isinstance(ln, (list, tuple)) else str(ln) for ln in (lines or [])] or \
        re.split(r"(?<=[.!?])\s+", text.strip())
    hit_words = [w for c in CAT_ORDER for w in hits.get(c, [])]

    def strength(p: str) -> float:
        low = p.lower()
        return sum(3 - min(2, i // 3) for i, w in enumerate(hit_words) if w in low) - (5 if QUESTION.match(p.strip()) else 0)
    best = max(pieces, key=strength) if pieces else text
    best = re.sub(r"\[[^\]]*\]", "", best)
    best = FILLER_START.sub("", best.strip())
    best = FILLER_START.sub("", best.strip())
    words = best.split()
    low = [w.lower().strip(",.!?") for w in words]
    n = len(words)
    spans = []
    for ph in hit_words:
        pw = ph.split()
        for i in range(n - len(pw) + 1):
            if low[i:i + len(pw)] == pw:
                spans.append((i, i + len(pw)))
    cands = {(0, min(n, n_words))}
    for a, b in spans:
        cands.add((max(0, b - n_words), b))
        cands.add((a, min(n, a + n_words)))

    def quality(c):
        a, b = c
        inside = sum(1 for x, y in spans if a <= x and y <= b)
        ends_clean = any(y == b for _, y in spans)
        bad_start = low[a] in ("and", "but", "so", "to", "of", "the", "that", "is") if a < n else True
        return inside * 2 + (1 if ends_clean else 0) - (1 if bad_start else 0) - 0.01 * a
    a, b = max(cands, key=quality) if words else (0, 0)
    frag = " ".join(words[a:b]).strip(" ,.;:-")
    return frag[:1].upper() + frag[1:] if frag else frag


def template_caption(who: Optional[str], hits: dict, people: list[str], text: str, lines: list | None = None) -> str:
    """Rule-based caption when no AI is available. Always 8-12 words, no emoji."""
    cat = next((c for c in CAT_ORDER if c in hits), None)
    other = next((p for p in people if p != who), None)
    if not who:  # speaker unknown: never guess who said it
        who = "Podcast guest" if other else (people[0] if people else "Podcast guest")
    if cat == "controversy" and other:
        base = f"{who} calls out {other}:"
    elif cat == "reveal" and other:
        base = f"{who} reveals the truth about {other}:"
    elif cat == "relationships" and other:
        base = f"{who} opens up about {other}:"
    elif other:
        base = f"{who} on {other}:"
    elif cat:
        base = f"{who} {VERB[cat]}:"
    else:
        base = f"{who}:"
    room = 12 - len(base.split())
    if room < 7 and other:
        base = f"{who} on {other}:"
        room = 12 - len(base.split())
    frag = _quote_fragment(text, max(4, room), hits, lines)
    cap = f'{base} "{frag}"' if frag else base
    return clean_caption(cap)


def hook_text(caption: str) -> str:
    """Short on-video hook (first 3 seconds): the first 5 words, upper-case."""
    words = re.sub(r'["“”]', "", caption).split()
    return " ".join(words[:5]).upper().rstrip(":,;-–— ")
