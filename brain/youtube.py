"""Everything that talks to YouTube: channel resolution, RSS, search, video info,
comments and transcripts. All free: public RSS feeds, yt-dlp, youtube-transcript-api."""
from __future__ import annotations

import difflib
import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from .util import Http, log

NS = {"a": "http://www.w3.org/2005/Atom",
      "yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/"}

CONSENT_COOKIES = {"CONSENT": "YES+cb.20240101-00-p0.en+FX+000", "SOCS": "CAI"}


class Blocked(Exception):
    """YouTube refused this machine (common on cloud servers). Laptop/phone can retry."""


class Unreachable(Exception):
    """Network problem (no answer at all). Temporary - nothing is marked as missing."""


class _QuietLogger:
    def debug(self, msg):  # noqa: D401
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        log.debug("yt-dlp: %s", msg)


_FILLER = {"the", "podcast", "show", "official", "with", "w", "pod", "channel", "tv", "fm", "s"}


def similarity(a: str, b: str) -> float:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()  # noqa: E731
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.95
    ta = [t for t in a.split() if t not in _FILLER]
    tb = set(t for t in b.split() if t not in _FILLER)
    if ta and tb:
        overlap = sum(1 for t in ta if t in tb) / len(ta)
        if overlap >= 0.66:
            return 0.9
        if " ".join(ta) in " ".join(sorted(tb)) or "".join(ta) in "".join(b.split()):
            return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------- channels
def resolve_channel(http: Http, name: str, handle: str = "", titles: Optional[list] = None,
                    mirrors=None) -> Optional[dict]:
    """Return {channel_id, title, method} or None. Verifies the title matches `name` (or `titles`)."""
    names = [name] + list(titles or [])

    def match(title: str, need: float = 0.6) -> bool:
        return any(similarity(n, title) >= need for n in names)
    if handle:
        r = http.get(f"https://www.youtube.com/@{handle.lstrip('@')}", cookies=CONSENT_COOKIES)
        if r is not None and r.status_code == 200:
            cid = _find(r.text, [r'"externalId":"(UC[\w-]{22})"',
                                 r'<meta itemprop="identifier" content="(UC[\w-]{22})"',
                                 r'channel_id=(UC[\w-]{22})', r'"channelId":"(UC[\w-]{22})"'])
            title = _find(r.text, [r'<meta property="og:title" content="([^"]+)"',
                                   r'"title":"([^"]+)","description"']) or ""
            if cid and (match(title, 0.5) or similarity(handle, title) >= 0.6):
                return {"channel_id": cid, "title": _unescape(title), "method": "handle"}
            if cid:
                log.info("Handle @%s resolved to '%s' which does not match '%s' - searching", handle, title, name)
        elif r is not None and r.status_code in (403, 429):
            raise Blocked(f"youtube {r.status_code}")
        elif r is None:
            raise Unreachable("youtube.com did not answer")
    # Fallback: search YouTube for the show name and pick the best-matching channel.
    blocked = None
    try:
        for e in search(f"{name} podcast", 8, by_date=False):
            ch = e.get("channel") or ""
            if e.get("channel_id") and match(ch):
                return {"channel_id": e["channel_id"], "title": ch, "method": "search"}
    except Blocked as b:
        blocked = b
    if mirrors is not None:
        for ch in mirrors.search_channels(name):
            if match(ch.get("title", "")):
                return {"channel_id": ch["channel_id"], "title": ch["title"], "method": "mirror"}
    if blocked:
        raise blocked
    return None


def _find(text: str, patterns: list[str]) -> Optional[str]:
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return None


def _unescape(s: str) -> str:
    import html
    return html.unescape(s)


# --------------------------------------------------------------------- rss
def fetch_rss(http: Http, channel_id: str) -> list[dict]:
    r = http.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    if r is None:
        return []
    if r.status_code in (403, 429):
        raise Blocked(f"rss {r.status_code}")
    if r.status_code != 200:
        return []
    return parse_rss(r.text)


def parse_rss(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    ch_title = root.findtext("a:title", default="", namespaces=NS)
    out = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", default="", namespaces=NS)
        if not vid:
            continue
        stats = e.find("media:group/media:community/media:statistics", NS)
        out.append({
            "video_id": vid,
            "title": e.findtext("a:title", default="", namespaces=NS),
            "published": e.findtext("a:published", default="", namespaces=NS),
            "channel_id": e.findtext("yt:channelId", default="", namespaces=NS),
            "channel_title": ch_title,
            "views": int(stats.get("views", "0")) if stats is not None else 0,
            "description": e.findtext("media:group/media:description", default="", namespaces=NS),
            "is_short": "/shorts/" in (e.find("a:link", NS).get("href", "") if e.find("a:link", NS) is not None else ""),
        })
    return out


# ------------------------------------------------------------------ yt-dlp
_COOKIE_FILE: Optional[str] = None


def cookie_file() -> Optional[str]:
    """Optional: YouTube cookies (Netscape format) from a THROWAWAY Google account, given as the
    YT_COOKIES secret. Helps when YouTube asks the cloud to 'confirm you're not a bot'."""
    global _COOKIE_FILE
    import os
    import tempfile
    raw = os.environ.get("YT_COOKIES", "").strip()
    if not raw:
        return None
    if _COOKIE_FILE is None:
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write(raw if raw.startswith("# Netscape") else "# Netscape HTTP Cookie File\n" + raw)
            f.write("\n")
        _COOKIE_FILE = path
    return _COOKIE_FILE


def attach_cookies(http: Http) -> bool:
    cf = cookie_file()
    if not cf:
        return False
    from http.cookiejar import MozillaCookieJar
    jar = MozillaCookieJar(cf)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as e:  # noqa: BLE001
        log.warning("YT_COOKIES could not be read: %s", e)
        return False
    for c in jar:
        http.s.cookies.set_cookie(c)
    return True


def _ydl(extra: Optional[dict] = None):
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
            "socket_timeout": 20, "retries": 2, "extractor_retries": 2, "ignoreerrors": False,
            "logger": _QuietLogger()}
    if cookie_file():
        opts["cookiefile"] = cookie_file()
    if extra:
        opts.update(extra)
    return yt_dlp.YoutubeDL(opts)


def _is_block(msg: str) -> bool:
    m = msg.lower()
    return any(k in m for k in ("sign in to confirm", "not a bot", "http error 429", "http error 403",
                                "too many requests", "confirm you're not a bot", "blocked"))


def _is_net(msg: str) -> bool:
    m = msg.lower()
    return any(k in m for k in ("unable to connect", "timed out", "temporary failure", "name or service not known",
                                "connection reset", "network is unreachable", "proxy"))


def search(query: str, n: int = 10, by_date: bool = True) -> list[dict]:
    """YouTube search via yt-dlp (no API key). by_date=True returns newest first."""
    prefix = "ytsearchdate" if by_date else "ytsearch"
    try:
        with _ydl({"extract_flat": "in_playlist"}) as y:
            res = y.extract_info(f"{prefix}{n}:{query}", download=False) or {}
    except Exception as e:  # noqa: BLE001
        if _is_block(str(e)):
            raise Blocked(str(e)) from e
        if _is_net(str(e)):
            raise Unreachable(str(e)) from e
        log.debug("search failed %s: %s", query, e)
        return []
    out = []
    for e in res.get("entries") or []:
        if not e or not e.get("id"):
            continue
        out.append({"video_id": e["id"], "title": e.get("title") or "", "channel": e.get("channel") or e.get("uploader") or "",
                    "channel_id": e.get("channel_id") or "", "duration": e.get("duration") or 0,
                    "views": e.get("view_count") or 0, "timestamp": e.get("timestamp") or e.get("release_timestamp")})
    return out


def video_info(video_id: str, with_comments: bool = True, max_comments: int = 300) -> dict:
    extra: dict[str, Any] = {}
    if with_comments:
        extra = {"getcomments": True,
                 "extractor_args": {"youtube": {"max_comments": [str(max_comments), str(max_comments), "0", "0"],
                                                "comment_sort": ["top"]}}}
    try:
        with _ydl(extra) as y:
            info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False) or {}
    except Exception as e:  # noqa: BLE001
        if _is_block(str(e)):
            raise Blocked(str(e)) from e
        if _is_net(str(e)):
            raise Unreachable(str(e)) from e
        if with_comments:  # comments can fail on their own; retry without them
            return video_info(video_id, with_comments=False)
        raise
    keep = ("id", "title", "description", "duration", "view_count", "like_count", "comment_count", "timestamp",
            "release_timestamp", "upload_date", "channel", "channel_id", "chapters", "heatmap", "live_status",
            "thumbnail", "automatic_captions", "subtitles", "categories", "tags", "age_limit")
    out = {k: info.get(k) for k in keep}
    out["comments"] = [{"text": c.get("text", ""), "likes": c.get("like_count") or 0}
                       for c in (info.get("comments") or [])][:max_comments]
    return out


# -------------------------------------------------------------- transcripts
def transcript(http: Http, video_id: str, info: Optional[dict] = None) -> tuple[list[dict], str]:
    """Return ([{start, dur, text}], source). Raises Blocked if this machine is refused."""
    blocked = False
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api import _errors as te
        try:
            ft = YouTubeTranscriptApi(http_client=http.s).fetch(video_id, languages=("en", "en-US", "en-GB"))
            segs = [{"start": float(s.start), "dur": float(s.duration), "text": s.text} for s in ft]
            if segs:
                return segs, "youtube-transcript-api"
        except (te.RequestBlocked, te.IpBlocked, te.PoTokenRequired):
            blocked = True
        except (te.TranscriptsDisabled, te.NoTranscriptFound, te.VideoUnavailable, te.VideoUnplayable):
            pass
        except Exception as e:  # noqa: BLE001
            log.debug("transcript api error %s: %s", video_id, e)
    except ImportError:
        pass
    # Fallback: caption track URLs that yt-dlp already found.
    if info:
        tracks = {}
        for src in (info.get("subtitles") or {}, info.get("automatic_captions") or {}):
            for lang, fmts in src.items():
                if lang.split("-")[0] == "en" and lang not in tracks:
                    tracks[lang] = fmts
        for lang, fmts in tracks.items():
            j3 = next((f for f in fmts if f.get("ext") == "json3"), None)
            if not j3:
                continue
            r = http.get(j3["url"])
            if r is not None and r.status_code == 200:
                segs = parse_json3(r.json())
                if segs:
                    return segs, f"captions-{lang}"
            elif r is not None and r.status_code in (403, 429):
                blocked = True
    if blocked:
        raise Blocked("transcript blocked")
    return [], "none"


def parse_json3(j: dict) -> list[dict]:
    out = []
    for ev in j.get("events", []) or []:
        segs = ev.get("segs")
        if not segs or "tStartMs" not in ev:
            continue
        text = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
        if not text:
            continue
        out.append({"start": ev["tStartMs"] / 1000.0, "dur": ev.get("dDurationMs", 2000) / 1000.0, "text": text})
    return out
