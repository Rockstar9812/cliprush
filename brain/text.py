"""Text helpers: cleaning captions, finding people, extracting the guest from a title."""
from __future__ import annotations

import re
from typing import Iterable, Optional

from .config import Person

NOISE = re.compile(r"\[(?:music|applause|inaudible|__|silence|foreign)\]|♪+", re.I)
LAUGH = re.compile(r"\[(?:laughter|laughs|laughing)\]|\((?:laughs|laughter|laughing)\)|\bha(?:ha)+\b|\blmao\b", re.I)
WS = re.compile(r"\s+")

STOP_TITLE = set("""the a an and or of on in at to for with w ft feat featuring vs x episode ep full interview podcast
show live new talks talk says said reveals reveal opens up about how why what who when where is are was were be
his her their my your our its it this that these those i you he she they we me him them us part pt season official
video clip clips reacts reaction speaks breaks down tells all exclusive uncut special edition opens explains responds
addresses details discusses gets goes calls admits shares recalls says reveals talks returns sits sitting joins
answers confirms denies claims slams roasts goes off finally first time why""".split())


def clean(text: str) -> str:
    text = NOISE.sub(" ", text or "")
    return WS.sub(" ", text).strip()


def norm(text: str) -> str:
    return WS.sub(" ", re.sub(r"[^a-z0-9$' ]+", " ", (text or "").lower())).strip()


def has_laugh(text: str) -> bool:
    return bool(LAUGH.search(text or ""))


class PeopleIndex:
    """Fast lookup of known people in text, using word-boundary regexes."""

    def __init__(self, people: Iterable[Person]):
        self.people = list(people)
        self._rx: list[tuple[re.Pattern, Person]] = []
        for p in self.people:
            for n in p.all_names:
                n2 = norm(n)
                if len(n2) < 3:
                    continue
                self._rx.append((re.compile(r"(?<![a-z0-9])" + re.escape(n2) + r"(?![a-z0-9])"), p))
        # Also match a distinctive last name for multi-word names (e.g. "Porter" for Michael Porter Jr)
        self._last: list[tuple[re.Pattern, Person]] = []
        for p in self.people:
            parts = norm(p.name).split()
            if len(parts) >= 2:
                last = parts[-1] if parts[-1] not in ("jr", "sr", "ii", "iii") else parts[-2]
                if len(last) >= 5:
                    self._last.append((re.compile(r"(?<![a-z0-9])" + re.escape(last) + r"(?![a-z0-9])"), p))

    def find(self, text: str, allow_last_name: bool = False) -> list[Person]:
        t = norm(text)
        found: dict[str, Person] = {}
        for rx, p in self._rx:
            if p.name not in found and rx.search(t):
                found[p.name] = p
        if allow_last_name:
            for rx, p in self._last:
                if p.name not in found and rx.search(t):
                    found[p.name] = p
        return sorted(found.values(), key=lambda p: p.tier)


def guest_from_title(title: str, show_name: str, people: PeopleIndex) -> tuple[Optional[str], int]:
    """Best guess of who the episode features. Returns (name, tier) where tier 9 = unknown person."""
    found = people.find(title, allow_last_name=True)
    host_like = set(norm(show_name).split())
    known = [p for p in found if not set(norm(p.name).split()) <= host_like]
    if known:
        return known[0].name, known[0].tier
    # Heuristic: runs of Capitalised Words that are not the show name or filler.
    cands = re.findall(r"((?:[A-Z][\w'.\-]+|[A-Z]{2,}\d*)(?:\s+(?:[A-Z][\w'.\-]+|Jr\.?|Sr\.?|III|II)){1,2})", title or "")
    for c in cands:
        words = [w for w in re.split(r"\s+", c) if w]
        # cut the run at the first filler/verb word ("Jaylen Waddle Opens Up" -> "Jaylen Waddle")
        for k in range(1, len(words)):
            if words[k].lower().strip(".") in STOP_TITLE:
                words = words[:k]
                break
        low = [w.lower().strip(".") for w in words]
        if all(w in STOP_TITLE for w in low):
            continue
        if set(low) <= host_like | STOP_TITLE:
            continue
        if len(words) >= 2 and not low[0] in STOP_TITLE:
            return " ".join(words), 9
    if found:  # only the host is named (e.g. a solo episode of the host's own show)
        return found[0].name, found[0].tier
    hosts = people.find(show_name, allow_last_name=True)
    if hosts:
        return hosts[0].name, hosts[0].tier
    return None, 9


def title_keywords(title: str, show_name: str) -> set[str]:
    host = set(norm(show_name).split())
    toks = [t for t in norm(title).split() if len(t) > 3 and t not in STOP_TITLE and t not in host and not t.isdigit()]
    return set(toks)


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]
