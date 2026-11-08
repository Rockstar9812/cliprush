"""X study without any X login (so nothing can ever be linked to your main account).

Sources, in order:
  1. X's public embed timeline (syndication) - newest ~12-20 posts per account, no login.
  2. FxTwitter public API - full stats (views, likes, reposts, replies) for a post id, no login.
  3. Apify free plan ($5/month credit) - backup + topic search, only if APIFY_TOKEN is set,
     with a hard monthly cap so it can never exceed the free credit.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Iterable, Optional

from .text import PeopleIndex, norm
from .util import UTC, Http, hours_since, iso, log, now_utc, parse_iso

EMOJI_RX = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF]")


# ------------------------------------------------------------ syndication
def fetch_syndication(http: Http, handle: str) -> Optional[list[dict]]:
    """None = blocked/unavailable, [] = no posts."""
    r = http.get(f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}",
                 headers={"Accept": "text/html"})
    if r is None or r.status_code != 200:
        return None
    return parse_syndication(r.text, handle)


def parse_syndication(html: str, handle: str = "") -> list[dict]:
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    out: dict[str, dict] = {}
    for tw in _walk_tweets(data):
        if tw.get("retweeted_status") or str(tw.get("full_text", tw.get("text", ""))).startswith("RT @"):
            continue
        p = _from_legacy(tw)
        if p and (not handle or p["account"].lower() == handle.lower()):
            out[p["id"]] = p
    return list(out.values())


def _walk_tweets(o: Any) -> Iterable[dict]:
    if isinstance(o, dict):
        if "id_str" in o and ("full_text" in o or "text" in o) and "user" in o:
            yield o
        for v in o.values():
            yield from _walk_tweets(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk_tweets(v)


def _from_legacy(tw: dict) -> Optional[dict]:
    media = (tw.get("extended_entities") or tw.get("entities") or {}).get("media") or []
    vids = [m for m in media if m.get("type") in ("video", "animated_gif")]
    if not vids:
        return None
    dur = (vids[0].get("video_info") or {}).get("duration_millis") or 0
    created = tw.get("created_at") or ""
    try:
        dt = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y")
        created = iso(dt)
    except ValueError:
        created = parse_iso(created) and iso(parse_iso(created)) or ""
    views = tw.get("views", {}).get("count") if isinstance(tw.get("views"), dict) else tw.get("view_count")
    return {"id": tw["id_str"], "account": (tw.get("user") or {}).get("screen_name", ""),
            "text": re.sub(r"\s*https://t\.co/\S+$", "", tw.get("full_text") or tw.get("text") or "").strip(),
            "created": created, "duration": round(dur / 1000.0, 1),
            "views": _int(views), "likes": _int(tw.get("favorite_count")),
            "reposts": _int(tw.get("retweet_count")), "replies": _int(tw.get("reply_count"))}


def _int(v: Any) -> int:
    try:
        return int(str(v).replace(",", "")) if v not in (None, "") else 0
    except ValueError:
        return 0


# --------------------------------------------------------------- fxtwitter
def fetch_fx(http: Http, account: str, post_id: str) -> Optional[dict]:
    r = http.get(f"https://api.fxtwitter.com/{account or 'i'}/status/{post_id}")
    if r is None or r.status_code != 200:
        return None
    try:
        return parse_fx(r.json())
    except ValueError:
        return None


def parse_fx(j: dict) -> Optional[dict]:
    t = j.get("tweet") or j.get("status")
    if not isinstance(t, dict):
        return None
    media = t.get("media") or {}
    vids = media.get("videos") or [m for m in media.get("all", []) if m.get("type") == "video"]
    created = ""
    if t.get("created_timestamp"):
        created = iso(datetime.fromtimestamp(int(t["created_timestamp"]), UTC))
    author = t.get("author") or {}
    return {"id": str(t.get("id", "")), "account": author.get("screen_name", ""), "text": t.get("text") or "",
            "created": created, "duration": float(vids[0].get("duration") or 0) if vids else 0.0,
            "has_video": bool(vids), "views": _int(t.get("views")), "likes": _int(t.get("likes")),
            "reposts": _int(t.get("retweets") or t.get("reposts")), "replies": _int(t.get("replies"))}


# ------------------------------------------------------------------- apify
class Apify:
    def __init__(self, http: Http, cfg, state: dict):
        self.http = http
        self.token = os.environ.get("APIFY_TOKEN", "").strip()
        self.actor = cfg.get("x_study.apify.actor", "xquik/x-tweet-scraper").replace("/", "~")
        self.cap_usd = float(cfg.get("x_study.apify.monthly_usd_cap", 4.5))
        self.per_k = float(cfg.get("x_study.apify.usd_per_1000", 0.15))
        month = now_utc().strftime("%Y-%m")
        self.state = state
        if state.get("month") != month:
            state.clear()
            state.update({"month": month, "items": 0, "usd": 0.0})

    @property
    def ok(self) -> bool:
        return bool(self.token)

    def remaining_items(self) -> int:
        return max(0, int((self.cap_usd - self.state.get("usd", 0.0)) / self.per_k * 1000))

    def run(self, handles: list[str], terms: list[str], max_items: int) -> list[dict]:
        n = min(max_items, self.remaining_items())
        if not self.ok or n <= 0:
            return []
        inp = {"handles": handles, "twitterHandles": handles, "usernames": handles, "from": handles,
               "searchTerms": terms, "queries": terms, "maxItems": n, "maxTweets": n, "limit": n,
               "sort": "Latest", "onlyVideo": True, "includeSearchTerms": False}
        r = self.http.post(f"https://api.apify.com/v2/acts/{self.actor}/run-sync-get-dataset-items",
                           params={"token": self.token, "timeout": 120}, json=inp)
        if r is None or r.status_code not in (200, 201):
            log.warning("Apify run failed: %s", None if r is None else f"{r.status_code} {r.text[:120]}")
            return []
        try:
            items = r.json()
        except ValueError:
            return []
        self.state["items"] = self.state.get("items", 0) + len(items)
        self.state["usd"] = round(self.state.get("usd", 0.0) + len(items) / 1000 * self.per_k, 4)
        return [p for p in (map_apify(i) for i in items if isinstance(i, dict)) if p]


def map_apify(i: dict) -> Optional[dict]:
    g = lambda *ks: next((i[k] for k in ks if k in i and i[k] not in (None, "")), None)  # noqa: E731
    pid = g("id", "tweetId", "id_str", "postId")
    if not pid:
        url = g("url", "twitterUrl", "tweetUrl") or ""
        m = re.search(r"/status/(\d+)", url)
        pid = m.group(1) if m else None
    if not pid:
        return None
    author = g("author", "user") or {}
    account = g("username", "screenName", "authorUsername", "handle") or \
        (author.get("userName") or author.get("username") or author.get("screen_name") if isinstance(author, dict) else "")
    media = g("media", "extendedEntities", "videos") or []
    has_video = bool(g("isVideo", "hasVideo")) or "video" in json.dumps(media)[:2000].lower()
    created = g("createdAt", "created_at", "date", "timestamp") or ""
    dt = parse_iso(str(created))
    if not dt:
        try:
            dt = datetime.strptime(str(created), "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            dt = None
    return {"id": str(pid), "account": str(account or ""), "text": str(g("text", "fullText", "full_text") or ""),
            "created": iso(dt) if dt else "", "duration": float(g("videoDuration", "duration") or 0),
            "has_video": has_video, "views": _int(g("viewCount", "views", "view_count", "impressions")),
            "likes": _int(g("likeCount", "likes", "favorite_count", "favoriteCount")),
            "reposts": _int(g("retweetCount", "retweets", "reposts", "retweet_count")),
            "replies": _int(g("replyCount", "replies", "reply_count"))}


# ---------------------------------------------------------------- the study
def tag_post(p: dict, people: PeopleIndex, podcasts: list) -> dict:
    t = p.get("text", "")
    p["people"] = [x.name for x in people.find(t, allow_last_name=True)][:4]
    nt = norm(t)
    p["podcast"] = next((pc.name for pc in podcasts if len(norm(pc.name)) > 5 and norm(pc.name) in nt), "")
    words = [w for w in re.sub(r"https?://\S+", "", t).split() if w.strip()]
    p["words"] = len(words)
    p["emoji"] = bool(EMOJI_RX.search(t))
    p["quote"] = '"' in t or "“" in t
    dt = parse_iso(p.get("created"))
    p["hour_utc"] = dt.hour if dt else None
    return p


def run_study(http: Http, cfg, xs: dict, people: PeopleIndex, podcasts: list) -> dict:
    """Mutates xs (persisted study state). Returns a short report for the Health tab."""
    posts: dict = xs.setdefault("posts", {})
    health: dict = {"sources": {}, "new": 0, "rechecked": 0, "at": iso(now_utc())}
    apify = Apify(http, cfg, xs.setdefault("apify", {}))
    blocked_accounts = []

    def upsert(p: dict, src: str) -> None:
        if not p or not p.get("id") or p.get("has_video") is False:
            return
        cur = posts.get(p["id"])
        snap = [iso(now_utc()), p.get("views", 0), p.get("likes", 0), p.get("reposts", 0), p.get("replies", 0)]
        if cur is None:
            p = tag_post({k: v for k, v in p.items() if k != "has_video"}, people, podcasts)
            p["snaps"] = [snap] if any(snap[1:]) else []
            p["src"] = src
            posts[p["id"]] = p
            health["new"] += 1
        else:
            for k in ("views", "likes", "reposts", "replies"):
                if p.get(k):
                    cur[k] = max(cur.get(k, 0), p[k])
            if p.get("duration") and not cur.get("duration"):
                cur["duration"] = p["duration"]
            if any(snap[1:]):
                cur.setdefault("snaps", []).append(snap)

    # 1) new posts per account via the free embed timeline
    for h in cfg.x_accounts:
        res = fetch_syndication(http, h)
        if res is None:
            blocked_accounts.append(h)
            health["sources"][h] = "blocked"
            continue
        health["sources"][h] = f"ok ({len(res)})"
        for p in res:
            upsert(p, "syndication")

    # 2) Apify backup for blocked accounts + trend search (only within the free credit)
    if apify.ok:
        terms = cfg.x_search_terms[: int(cfg.get("x_study.apify.search_terms_per_round", 3))]
        if blocked_accounts:
            for p in apify.run(blocked_accounts, [], 20 * len(blocked_accounts)):
                upsert(p, "apify")
        if terms:
            for p in apify.run([], terms, 40):
                if p.get("views", 0) >= 100_000:  # only keep clearly viral posts from search
                    upsert(p, "apify-search")
        health["apify"] = f"${apify.state.get('usd', 0):.2f} of ${apify.cap_usd:.2f} used this month"
    else:
        health["apify"] = "not set (optional)"

    # 3) re-check stats at 1h / 6h / 24h / 72h through FxTwitter
    marks = [float(x) for x in cfg.get("x_study.recheck_after_hours", [1, 6, 24, 72])]
    budget = 250
    fx_fail = 0
    for p in sorted(posts.values(), key=lambda x: x.get("created", ""), reverse=True):
        if budget <= 0:
            break
        age = hours_since(p.get("created"))
        done = p.setdefault("checked", [])
        due = [m for m in marks if age >= m and m not in done]
        if not due:
            continue
        fx = fetch_fx(http, p.get("account", ""), p["id"])
        budget -= 1
        if fx is None:
            fx_fail += 1
            if fx_fail >= 8 and health["rechecked"] == 0:
                health["sources"]["fxtwitter"] = "unavailable"
                break
            continue
        upsert(fx, "fx")
        p["checked"] = sorted(set(done + due))
        health["rechecked"] += 1
    health["sources"].setdefault("fxtwitter", "ok" if health["rechecked"] or fx_fail == 0 else "errors")

    # prune
    keep_h = float(cfg.get("x_study.keep_days", 120)) * 24
    for pid in [k for k, v in posts.items() if hours_since(v.get("created")) > keep_h]:
        posts.pop(pid, None)
    xs["last_run"] = iso(now_utc())
    xs["health"] = health
    log.info("X study: %d new, %d rechecked, %d tracked", health["new"], health["rechecked"], len(posts))
    return health


def views_at(p: dict, hours: float) -> Optional[int]:
    """Views at roughly `hours` after posting, from the snapshots (None if unknown)."""
    created = parse_iso(p.get("created"))
    if not created:
        return None
    best = None
    for s in p.get("snaps", []):
        t = parse_iso(s[0])
        if not t:
            continue
        h = (t - created).total_seconds() / 3600
        if abs(h - hours) <= max(0.6, hours * 0.35) and s[1]:
            if best is None or abs(h - hours) < best[0]:
                best = (abs(h - hours), int(s[1]))
    return best[1] if best else None
