"""Shared helpers: logging, HTTP with retries, time zones, small text utils."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

log = logging.getLogger("cliprush")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(_h)
    log.setLevel(os.environ.get("CLIPRUSH_LOG", "INFO"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def hours_since(s: Optional[str], ref: Optional[datetime] = None) -> float:
    dt = parse_iso(s)
    if not dt:
        return 1e9
    return ((ref or now_utc()) - dt).total_seconds() / 3600.0


def short_id(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s[:n].strip("-") or "clip"


def fmt_ts(sec: float) -> str:
    sec = int(max(0, sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Http:
    """requests session with retries, backoff and a per-host politeness delay."""

    def __init__(self, min_gap: float = 0.4, timeout: float = 20.0):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        self.timeout = timeout
        self.min_gap = min_gap
        self._last: dict[str, float] = {}
        self.blocked_hosts: set[str] = set()

    def _wait(self, host: str) -> None:
        gap = time.time() - self._last.get(host, 0)
        if gap < self.min_gap:
            time.sleep(self.min_gap - gap + random.random() * 0.2)
        self._last[host] = time.time()

    def get(self, url: str, *, tries: int = 3, **kw) -> Optional[requests.Response]:
        host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
        kw.setdefault("timeout", self.timeout)
        for i in range(tries):
            self._wait(host)
            try:
                r = self.s.get(url, **kw)
            except requests.RequestException as e:
                log.debug("GET %s failed: %s", url, e)
                time.sleep(1.5 * (i + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2.0 * (i + 1) + random.random())
                continue
            if r.status_code in (401, 403):
                self.blocked_hosts.add(host)
            return r
        return None

    def post(self, url: str, *, tries: int = 3, **kw) -> Optional[requests.Response]:
        host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
        kw.setdefault("timeout", self.timeout * 3)
        for i in range(tries):
            self._wait(host)
            try:
                r = self.s.post(url, **kw)
            except requests.RequestException as e:
                log.debug("POST %s failed: %s", url, e)
                time.sleep(2.0 * (i + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(3.0 * (i + 1) + random.random())
                continue
            return r
        return None


def jdump(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"), default=str)
