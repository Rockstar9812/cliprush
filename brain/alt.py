"""Free fallback when YouTube blocks the cloud ("Sign in to confirm you're not a bot").

Public Invidious and Piped mirrors fetch YouTube from their own servers and give back captions,
comments and video details. They are free and need no account. Instances come and go, so the
live instance lists are read every run and the healthiest ones are tried first."""
from __future__ import annotations

import html
import re
import time
from datetime import datetime
from typing import Optional

from .util import UTC, Http, log

INVIDIOUS_LIST = "https://api.invidious.io/instances.json?sort_by=type,health"
PIPED_LIST = "https://piped-instances.kavin.rocks/"
FALLBACK_INVIDIOUS = ["https://inv.nadeko.net", "https://invidious.nerdvpn.de", "https://yewtu.be",
                      "https://invidious.f5.si", "https://iv.melmac.space"]
FALLBACK_PIPED = ["https://pipedapi.kavin.rocks", "https://api.piped.private.coffee", "https://pipedapi.adminforge.de"]
TAG = re.compile(r"<[^>]+>")
CUE = re.compile(r"(\d{1,2}:)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{1,2}:)?(\d{2}):(\d{2})[.,](\d{3})")


class Mirrors:
    def __init__(self, http: Http, state: dict):
        self.http = http
        self.state = state  # persisted: {"good": {url: last_ok_iso}, "bad": {url: count}}
        self._inv: Optional[list[str]] = None
        self._piped: Optional[list[str]] = None

    # ------------------------------------------------------------ instance lists
    def invidious(self) -> list[str]:
        if self._inv is None:
            urls = []
            r = self.http.get(INVIDIOUS_LIST, tries=1)
            try:
                for name, meta in (r.json() if r is not None and r.status_code == 200 else []):
                    if isinstance(meta, dict) and meta.get("type") == "https" and meta.get("api") and meta.get("uri"):
                        urls.append(meta["uri"].rstrip("/"))
            except ValueError:
                pass
            self._inv = self._order(urls + FALLBACK_INVIDIOUS)
        return self._inv

    def piped(self) -> list[str]:
        if self._piped is None:
            urls = []
            r = self.http.get(PIPED_LIST, tries=1)
            try:
                for it in (r.json() if r is not None and r.status_code == 200 else []):
                    if isinstance(it, dict) and it.get("api_url"):
                        urls.append(it["api_url"].rstrip("/"))
            except ValueError:
                pass
            self._piped = self._order(urls + FALLBACK_PIPED)
        return self._piped

    def _order(self, urls: list[str]) -> list[str]:
        seen, out = set(), []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        good = self.state.setdefault("good", {})
        bad = self.state.setdefault("bad", {})
        return sorted(out, key=lambda u: (u not in good, bad.get(u, 0), -len(good.get(u, ""))))

    def _ok(self, u: str) -> None:
        self.state.setdefault("good", {})[u] = datetime.now(UTC).isoformat()
        self.state.setdefault("bad", {}).pop(u, None)

    def _fail(self, u: str) -> None:
        b = self.state.setdefault("bad", {})
        b[u] = b.get(u, 0) + 1
        self.state.setdefault("good", {}).pop(u, None)

    # ------------------------------------------------------------------ search
    def _inv_search(self, params: dict, tries: int = 3) -> list:
        for base in self.invidious()[:tries]:
            r = self.http.get(f"{base}/api/v1/search", params=params, tries=1, timeout=12)
            if r is not None and r.status_code == 200:
                try:
                    res = r.json()
                    if isinstance(res, list):
                        self._ok(base)
                        return res
                except ValueError:
                    pass
            self._fail(base)
        return []

    def search_videos(self, query: str, n: int = 6) -> list[dict]:
        out = []
        for v in self._inv_search({"q": query, "sort_by": "upload_date", "type": "video"}):
            if isinstance(v, dict) and v.get("videoId"):
                out.append({"video_id": v["videoId"], "title": v.get("title", ""), "channel": v.get("author", ""),
                            "channel_id": v.get("authorId", ""), "duration": v.get("lengthSeconds") or 0,
                            "views": v.get("viewCount") or 0, "timestamp": v.get("published")})
        return out[:n]

    def search_channels(self, name: str) -> list[dict]:
        return [{"channel_id": c["authorId"], "title": c.get("author", "")}
                for c in self._inv_search({"q": name, "type": "channel"}, tries=2)
                if isinstance(c, dict) and c.get("authorId")][:6]

    # ------------------------------------------------------------------ fetch
    def video(self, video_id: str, max_tries: int = 6, budget_s: float = 75) -> Optional[tuple[dict, list[dict]]]:
        """Returns (info, transcript_segments) like yt-dlp would, or None if every mirror failed."""
        t0 = time.time()
        tries = 0
        for base in self.invidious():
            if tries >= max_tries or time.time() - t0 > budget_s:
                break
            tries += 1
            res = self._invidious(base, video_id)
            if res:
                self._ok(base)
                return res
            self._fail(base)
        for base in self.piped():
            if tries >= max_tries + 3 or time.time() - t0 > budget_s:
                break
            tries += 1
            res = self._piped(base, video_id)
            if res:
                self._ok(base)
                return res
            self._fail(base)
        log.info("All free mirrors failed for %s", video_id)
        return None

    def _invidious(self, base: str, vid: str) -> Optional[tuple[dict, list[dict]]]:
        r = self.http.get(f"{base}/api/v1/videos/{vid}", tries=1, timeout=15)
        if r is None or r.status_code != 200:
            return None
        try:
            v = r.json()
        except ValueError:
            return None
        if not isinstance(v, dict) or v.get("error"):
            return None
        caps = v.get("captions") or []
        cap = _pick_caption(caps, lambda c: (c.get("language_code") or c.get("languageCode") or ""), lambda c: c.get("label", ""))
        segs: list[dict] = []
        if cap:
            url = cap["url"] if cap["url"].startswith("http") else base + cap["url"]
            cr = self.http.get(url, tries=1, timeout=15)
            if cr is not None and cr.status_code == 200:
                segs = parse_vtt(cr.text)
        if not segs:
            return None
        comments = []
        cr = self.http.get(f"{base}/api/v1/comments/{vid}", params={"sort_by": "top"}, tries=1, timeout=15)
        if cr is not None and cr.status_code == 200:
            try:
                comments = [{"text": html.unescape(TAG.sub("", c.get("content") or c.get("contentHtml") or "")),
                             "likes": int(c.get("likeCount") or 0)} for c in cr.json().get("comments", [])]
            except (ValueError, AttributeError):
                pass
        info = {"id": vid, "title": v.get("title", ""), "description": v.get("description", ""),
                "duration": v.get("lengthSeconds") or 0, "view_count": v.get("viewCount") or 0,
                "timestamp": v.get("published"), "channel": v.get("author"), "channel_id": v.get("authorId"),
                "chapters": None, "heatmap": None, "comments": comments, "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                "live_status": "is_live" if v.get("liveNow") else ("is_upcoming" if v.get("isUpcoming") else "not_live"),
                "via": base}
        return info, segs

    def _piped(self, base: str, vid: str) -> Optional[tuple[dict, list[dict]]]:
        r = self.http.get(f"{base}/streams/{vid}", tries=1, timeout=15)
        if r is None or r.status_code != 200:
            return None
        try:
            v = r.json()
        except ValueError:
            return None
        if not isinstance(v, dict) or v.get("error"):
            return None
        subs = v.get("subtitles") or []
        cap = _pick_caption(subs, lambda c: c.get("code") or "", lambda c: c.get("name") or "")
        segs: list[dict] = []
        if cap and cap.get("url"):
            cr = self.http.get(cap["url"], tries=1, timeout=15)
            if cr is not None and cr.status_code == 200:
                segs = parse_vtt(cr.text) if "-->" in cr.text else parse_ttml(cr.text)
        if not segs:
            return None
        comments = []
        cr = self.http.get(f"{base}/comments/{vid}", tries=1, timeout=15)
        if cr is not None and cr.status_code == 200:
            try:
                comments = [{"text": html.unescape(TAG.sub("", c.get("commentText") or "")), "likes": int(c.get("likeCount") or 0)}
                            for c in cr.json().get("comments", [])]
            except (ValueError, AttributeError):
                pass
        chapters = [{"start_time": c.get("start", 0), "title": c.get("title", "")} for c in v.get("chapters") or []]
        for i, c in enumerate(chapters):
            c["end_time"] = chapters[i + 1]["start_time"] if i + 1 < len(chapters) else 1e12
        ts = None
        try:
            ts = int(datetime.fromisoformat(str(v.get("uploadDate", "")).replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
        info = {"id": vid, "title": v.get("title", ""), "description": v.get("description", ""),
                "duration": v.get("duration") or 0, "view_count": v.get("views") or 0, "timestamp": ts,
                "channel": v.get("uploader"), "chapters": chapters or None, "heatmap": None, "comments": comments,
                "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                "live_status": "is_live" if v.get("livestream") else "not_live", "via": base}
        return info, segs


def _pick_caption(caps: list, lang, label) -> Optional[dict]:
    en = [c for c in caps if isinstance(c, dict) and str(lang(c)).lower().startswith("en")]
    if not en:
        return None
    manual = [c for c in en if "auto" not in label(c).lower() and not c.get("autoGenerated")]
    return (manual or en)[0]


def _sec(h, m, s, ms) -> float:
    return (int(h.rstrip(":")) if h else 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_vtt(text: str) -> list[dict]:
    """WebVTT -> segments. Handles YouTube auto-captions, which repeat the previous line in every cue."""
    out: list[dict] = []
    last = ""
    blocks = re.split(r"\n\s*\n", text.replace("\r", ""))
    for b in blocks:
        m = CUE.search(b)
        if not m:
            continue
        start = _sec(*m.group(1, 2, 3, 4))
        end = _sec(*m.group(5, 6, 7, 8))
        if end - start < 0.05:
            continue
        lines = [html.unescape(TAG.sub("", ln)).strip() for ln in b[m.end():].split("\n")]
        lines = [ln for ln in lines if ln]
        if not lines:
            continue
        txt = lines[-1]
        if txt == last:
            continue
        last = txt
        out.append({"start": start, "dur": max(0.3, end - start), "text": txt})
    return out


def parse_ttml(text: str) -> list[dict]:
    out = []
    for m in re.finditer(r'<p[^>]*begin="([\d:.]+)"[^>]*end="([\d:.]+)"[^>]*>(.*?)</p>', text, re.S):
        def t(s):
            parts = [float(x) for x in s.split(":")]
            return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
        txt = html.unescape(TAG.sub(" ", m.group(3))).strip()
        if txt:
            out.append({"start": t(m.group(1)), "dur": max(0.3, t(m.group(2)) - t(m.group(1))), "text": re.sub(r"\s+", " ", txt)})
    return out
