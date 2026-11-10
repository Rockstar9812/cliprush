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


SEP = re.compile(r"\s*(?:\||•|–|—| - |:|#\d+|\bft\.?|\bfeat\.?|\bw/)\s*", re.I)


def guest_from_title(title: str, show_name: str, people: PeopleIndex) -> tuple[Optional[str], int]:
    """Best guess of who the episode features. Returns (name, tier) where tier 9 = unknown person.
    Known people must match their full name (or alias) - a shared surname is not enough."""
    found = people.find(title, allow_last_name=False)
    host_like = set(norm(show_name).split())
    known = [p for p in found if not set(norm(p.name).split()) <= host_like]
    if known:
        return known[0].name, known[0].tier
    # Unknown person: only trust a Capitalised name that sits right at a separator
    # ("Jaylen Waddle | Bussin", "Ep 12 - Jaylen Waddle", "Jaylen Waddle Opens Up | ...").
    for seg in [x for x in SEP.split(title or "") if x and x.strip()]:
        seg = seg.strip(" \"'“”")
        if set(norm(seg).split()) <= host_like | STOP_TITLE:
            continue
        m = re.match(r"((?:[A-Z][a-z'’.\-]+|[A-Z]{2,}\d*)(?:\s+(?:[A-Z][a-z'’.\-]+|Jr\.?|Sr\.?|III|II)){1,2})(?=\s|$)", seg)
        if not m:
            continue
        words = m.group(1).split()
        for k in range(1, len(words)):
            if words[k].lower().strip(".") in STOP_TITLE:
                words = words[:k]
                break
        low = [w.lower().strip(".") for w in words]
        rest = seg[len(m.group(1)):].strip().split()
        whole = not rest
        then_verb = bool(rest) and rest[0].lower().strip(".,") in STOP_TITLE
        if len(words) >= 2 and not any(w in STOP_TITLE or w in COMMON for w in low) and (whole or then_verb or len(words) < len(m.group(1).split())):
            same = people.find(" ".join(words))
            if same:  # it's a known person after all (e.g. the host of their own show)
                return same[0].name, same[0].tier
            return " ".join(words), 9
    if found:  # only the host is named (e.g. a solo episode of the host's own show)
        return found[0].name, found[0].tier
    hosts = people.find(show_name, allow_last_name=False)
    if hosts:
        return hosts[0].name, hosts[0].tier
    return None, 9


COMMON = set("""new big old real true last first best worst good bad black white red blue american british jersey york
winner loser cash money million billion can take took wait wins won lost game games team world news live today
year years day days night man men woman women girl girls boy boys guy guys king queen god love life death dead
murdered killed rap hip hop music movie movies show shows song songs album full official clip clips trailer""".split())


def title_keywords(title: str, show_name: str) -> set[str]:
    host = set(norm(show_name).split())
    toks = [t for t in norm(title).split() if len(t) > 3 and t not in STOP_TITLE and t not in host and not t.isdigit()]
    return set(toks)


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]
