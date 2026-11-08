"""Realistic offline fixtures (YouTube RSS, transcript, comments, X embed timeline, FxTwitter)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

UTC = timezone.utc


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def rss(channel_id="UCabcdefghijklmnopqrstuv", title="Club Shay Shay", hours_ago=2, vid="vid00000001",
        ep_title="Katt Williams | Club Shay Shay"):
    from xml.sax.saxutils import escape
    title, ep_title = escape(title), escape(ep_title)
    pub = iso(datetime.now(UTC) - timedelta(hours=hours_ago))
    old = iso(datetime.now(UTC) - timedelta(days=9))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
 <title>{title}</title>
 <entry><yt:videoId>{vid}</yt:videoId><yt:channelId>{channel_id}</yt:channelId><title>{ep_title}</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v={vid}"/><published>{pub}</published>
  <media:group><media:description>Full episode</media:description><media:community><media:statistics views="250000"/></media:community></media:group></entry>
 <entry><yt:videoId>short000001</yt:videoId><yt:channelId>{channel_id}</yt:channelId><title>clip</title>
  <link rel="alternate" href="https://www.youtube.com/shorts/short000001"/><published>{pub}</published>
  <media:group><media:community><media:statistics views="10"/></media:community></media:group></entry>
 <entry><yt:videoId>old00000001</yt:videoId><yt:channelId>{channel_id}</yt:channelId><title>Old ep</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=old00000001"/><published>{old}</published>
  <media:group><media:community><media:statistics views="10"/></media:community></media:group></entry>
</feed>"""


FILLER = ["we were talking about the weekend and the weather was nice out there",
          "i went to the store and got some food it was a regular day for me",
          "the team practiced all morning and then we had lunch together you know",
          "my family came to visit and we watched a movie at home that night",
          "the traffic was really slow on the highway so i was late to the meeting",
          "we talked about training and staying healthy and drinking water every day"]


def transcript(duration=3600):
    segs = []
    t = 0.0
    i = 0
    while t < duration:
        segs.append({"start": t, "dur": 3.5, "text": FILLER[i % len(FILLER)]})
        t += 3.6
        i += 1

    def put(at, lines):
        nonlocal segs
        segs = [s for s in segs if not (at - 0.1 <= s["start"] < at + 3.6 * len(lines))]
        for k, ln in enumerate(lines):
            segs.append({"start": at + 3.6 * k, "dur": 3.5, "text": ln})
        segs.sort(key=lambda s: s["start"])

    # sponsor read (should never be picked)
    put(600, ["this episode is brought to you by prizepicks use code shay for a first deposit match",
              "download the app and sign up today link in the description crazy deal million"])
    # the viral moment (should be #1)
    put(1830, ["did you ever have a problem with kevin hart",
               "man the truth is kevin hart is an industry plant and i never told anyone this",
               "he lied to everybody he set me up and stole my whole bit that's crazy",
               "[Laughter] i'm dead serious on god he owe me money a million dollars"])
    # a second, weaker moment
    put(2700, ["who is the goat in your opinion lebron or jordan",
               "lebron is overrated man jordan is the greatest of all time no debate"])
    return segs


def info(vid="vid00000001", duration=3600, hours_ago=2):
    ts = int((datetime.now(UTC) - timedelta(hours=hours_ago)).timestamp())
    comments = [{"text": "30:35 bro really said kevin hart is an industry plant 💀", "likes": 900},
                {"text": "30:40 HE SAID IT", "likes": 300},
                {"text": "the 30:38 part is insane", "likes": 120},
                {"text": "45:05 lebron overrated?? nah", "likes": 40},
                {"text": "great episode", "likes": 5}]
    return {"id": vid, "title": "Katt Williams | Club Shay Shay", "description": "", "duration": duration,
            "view_count": 250000, "timestamp": ts, "channel": "Club Shay Shay", "channel_id": "UCabcdefghijklmnopqrstuv",
            "chapters": [{"start_time": 1800, "end_time": 2400, "title": "Kevin Hart beef"}],
            "heatmap": None, "live_status": "not_live", "thumbnail": "", "comments": comments,
            "automatic_captions": {}, "subtitles": {}, "age_limit": 0}


def syndication_html(handle="fearedbuck", n=6, hours_ago=30):
    entries = []
    for k in range(n):
        dt = datetime.now(UTC) - timedelta(hours=hours_ago + k)
        entries.append({"type": "tweet", "content": {"tweet": {
            "id_str": f"18{k:017d}", "full_text": f"Katt Williams calls out Kevin Hart on Club Shay Shay {k} https://t.co/abc",
            "created_at": dt.strftime("%a %b %d %H:%M:%S +0000 %Y"), "favorite_count": 1000 * (k + 1),
            "retweet_count": 100, "reply_count": 50, "user": {"screen_name": handle},
            "extended_entities": {"media": [{"type": "video", "video_info": {"duration_millis": 14000}}]}}}})
    entries.append({"type": "tweet", "content": {"tweet": {"id_str": "1799", "full_text": "RT @x: hi", "user": {"screen_name": handle},
                    "retweeted_status": {}, "created_at": "Mon Nov 02 10:00:00 +0000 2026"}}})
    data = {"props": {"pageProps": {"timeline": {"entries": entries}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def fx_json(pid, views=1500000, account="fearedbuck", hours_ago=30):
    ts = int((datetime.now(UTC) - timedelta(hours=hours_ago)).timestamp())
    return {"code": 200, "tweet": {"id": pid, "text": "Katt Williams calls out Kevin Hart", "created_timestamp": ts,
                                   "likes": 20000, "retweets": 3000, "replies": 900, "views": views,
                                   "author": {"screen_name": account}, "media": {"videos": [{"duration": 14.0}]}}}


class FakeResp:
    def __init__(self, status=200, text="", js=None, content=b""):
        self.status_code = status
        self.text = text if text else (json.dumps(js) if js is not None else "")
        self._js = js
        self.content = content or self.text.encode()
        self.ok = status < 400

    def json(self):
        return self._js if self._js is not None else json.loads(self.text)


class FakeHttp:
    """Routes URLs to canned responses. Unknown URLs return 404."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []
        self.blocked_hosts = set()
        import requests
        self.s = requests.Session()

    def get(self, url, **kw):
        self.calls.append(url)
        for k, v in self.routes.items():
            if k in url:
                return v(url) if callable(v) else v
        return FakeResp(404, "nf")

    def post(self, url, **kw):
        self.calls.append(url)
        for k, v in self.routes.items():
            if k in url:
                return v(url, kw) if callable(v) else v
        return FakeResp(404, "nf")
