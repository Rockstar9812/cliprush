"""One ClipRush cycle: discover -> analyse -> pick moments -> caption -> batch -> study X -> publish."""
from __future__ import annotations

import math
import re
import os
import time
from datetime import datetime, timedelta
from typing import Optional

from . import youtube as yt
from .ai import AI
from .alt import Mirrors
from .captions import caption_ok, clean_caption, hook_text, template_caption
from .config import Config
from .learn import build_playbook
from .scoring import Moment, episode_hotness, score_episode
from .store import load, load_feedback, make_store, merge_brain, save
from .text import PeopleIndex, guest_from_title
from .util import IST, UTC, Http, fmt_ts, hours_since, iso, log, now_utc, parse_iso, short_id
from .xstudy import run_study

STAGES = ["new", "info", "no_transcript", "needs_local", "done", "skipped"]
RESCORE_AT_H = [0.75, 3.0, 8.0]  # comments keep arriving; re-read them and re-rank


class Cycle:
    def __init__(self, mode: str = "cloud", store=None, password: Optional[str] = None, deadline_s: float = 600):
        self.mode = mode
        self.cfg = Config()
        self.http = Http()
        self.people = PeopleIndex(self.cfg.people)
        self.person_tier = {p.name: p.tier for p in self.cfg.people}
        self.store = store or make_store()
        self.password = password or os.environ.get("CLIPRUSH_PASSWORD", "")
        if len(self.password) < 8:
            raise SystemExit("CLIPRUSH_PASSWORD must be set (8+ characters).")
        self.t0 = time.time()
        self.deadline = deadline_s
        self.yt_blocked = False      # YouTube AND the free mirrors failed -> laptop needed
        self.direct_blocked = False  # YouTube refused us directly; mirrors still tried
        self.events: list[str] = []
        self.via: dict[str, int] = {}
        if yt.attach_cookies(self.http):
            log.info("Using YT_COOKIES for YouTube requests")

    # ----------------------------------------------------------------- utils
    def time_left(self) -> float:
        return self.deadline - (time.time() - self.t0)

    def note(self, msg: str) -> None:
        log.info(msg)
        self.events.append(f"{datetime.now(IST).strftime('%H:%M')} {msg}")

    # ------------------------------------------------------------------- run
    def run(self, tasks: tuple[str, ...] = ("watch", "study"), video: str = "") -> dict:
        head0 = self.store.head()
        self.app, self.brain = load(self.store, self.password)
        b = self.brain
        for k in ("episodes", "clips", "channels", "xstudy", "ai_usage", "health", "playbook", "mirrors"):
            b.setdefault(k, {})
        self.feedback = load_feedback(self.store)
        self.ai = AI(self.http, self.cfg, b["ai_usage"])
        self.mirrors = Mirrors(self.http, b["mirrors"])
        b["last_manual"] = None
        if video:
            self.add_manual(video)

        if "watch" in tasks:
            for step in (self.discover, self.process):
                try:
                    step()
                except yt.Blocked as e:
                    self.direct_blocked = True
                    self.note(f"YouTube refused this {self.mode} machine ({str(e)[:70]})"
                              + (" - using free mirrors" if step == self.discover else ""))
                    if step == self.discover:
                        continue
                    self.yt_blocked = True
                except yt.Unreachable as e:
                    self.note(f"Network problem reaching YouTube ({str(e)[:80]}) - retrying next run")
            if self.via:
                self.note("Read via: " + ", ".join(f"{k} x{v}" for k, v in self.via.items()))
        if "study" in tasks and self.study_due():
            try:
                run_study(self.http, self.cfg, b["xstudy"], self.people, self.cfg.podcasts)
                b["playbook"] = build_playbook(b["xstudy"].get("posts", {}))
            except Exception as e:  # noqa: BLE001 - never let the study break clip delivery
                self.note(f"X study error: {e}")
        self.prune()
        self.app = self.build_app()
        b["health"] = self.app["health"]

        # optimistic concurrency: if someone else wrote meanwhile, merge instead of overwriting
        if head0 and self.store.head() != head0:
            log.info("Data changed during this run - merging")
            _, theirs = load(self.store, self.password)
            self.brain = merge_brain(self.brain, theirs)
            self.app = self.build_app()
        save(self.store, self.app, self.brain, self.password, f"ClipRush {self.mode} {iso(now_utc())}")
        return self.app["health"]

    # ---------------------------------------------------------------- manual
    def add_manual(self, url: str) -> None:
        import re as _re
        m = _re.search(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([\w-]{11})", url) or _re.fullmatch(r"\s*([\w-]{11})\s*", url)
        if not m:
            self.note(f"Manual: could not read a YouTube video id from '{url[:60]}'")
            return
        vid = m.group(1)
        e = self.brain["episodes"].get(vid)
        if e and e.get("stage") == "done":
            e["_rescore"] = ["manual"]
        e = e or {"id": vid, "title": "(manual)", "show": "Manual", "cat": "manual", "podcast_tier": 2, "guest": None,
                  "guest_tier": 9, "published": "", "views": 0, "found": iso(now_utc())}
        e.update({"stage": "new" if e.get("stage") != "done" else "done", "source": "manual", "manual": True,
                  "updated": iso(now_utc()), "tries": 0})
        self.brain["episodes"][vid] = e
        self.brain["last_manual"] = {"video": vid, "at": iso(now_utc())}
        self.note(f"Manual request: clipping https://youtu.be/{vid}")

    # ------------------------------------------------------------- discovery
    def resolve_channels(self) -> None:
        ch = self.brain["channels"]
        missing = []
        for p in self.cfg.podcasts:
            rec = ch.get(p.name, {})
            if p.channel_id:
                ch[p.name] = {"channel_id": p.channel_id, "title": p.name, "method": "config", "updated": iso(now_utc())}
                continue
            age = hours_since(rec.get("checked"))
            if rec.get("channel_id") and age < 24 * 14:
                continue
            if not rec.get("channel_id") and age < 24 and rec.get("checked"):
                continue
            if self.time_left() < 120:
                break
            try:
                res = yt.resolve_channel(self.http, p.name, p.handle, p.titles, self.mirrors)
            except yt.Unreachable:
                self.note("YouTube unreachable right now (network) - will retry next run")
                break
            ch[p.name] = {**(res or {"channel_id": "", "title": "", "method": "not found"}),
                          "checked": iso(now_utc()), "updated": iso(now_utc())}
            if not res:
                missing.append(p.name)
        if missing:
            self.note(f"Could not find {len(missing)} YouTube channel(s): {', '.join(missing[:4])}"
                      f"{' ...' if len(missing) > 4 else ''} (see Health tab)")

    def discover(self) -> None:
        self.resolve_channels()
        eps = self.brain["episodes"]
        max_age = float(self.cfg.get("discovery.max_episode_age_hours", 48))
        for p in self.cfg.podcasts:
            cid = self.brain["channels"].get(p.name, {}).get("channel_id")
            if not cid:
                continue
            for e in yt.fetch_rss(self.http, cid):
                if e["video_id"] in eps or e.get("is_short") or hours_since(e["published"]) > max_age:
                    continue
                guest, gt = guest_from_title(e["title"], p.name, self.people)
                eps[e["video_id"]] = {"id": e["video_id"], "title": e["title"], "show": p.name, "cat": p.cat,
                                      "podcast_tier": p.tier, "guest": guest, "guest_tier": gt, "published": e["published"],
                                      "views": e["views"], "stage": "new", "source": "channel",
                                      "found": iso(now_utc()), "updated": iso(now_utc())}
                self.note(f"New episode: {p.name} - {e['title'][:70]}")
        self.search_people()

    def search_people(self) -> None:
        b = self.brain
        every = float(self.cfg.get("discovery.people_search_every_minutes", 60)) / 60
        if hours_since(b.get("people_search_at")) < every:
            return
        per = int(self.cfg.get("discovery.people_per_search_round", 12))
        trending = b.get("playbook", {}).get("trending_people", [])
        ordered = [p for p in self.cfg.people if p.name in trending] + \
                  sorted([p for p in self.cfg.people if p.name not in trending], key=lambda p: p.tier)
        start = int(b.get("people_cursor", 0)) % max(1, len(ordered))
        batch = (ordered[start:] + ordered[:start])[:per]
        b["people_cursor"] = start + per
        b["people_search_at"] = iso(now_utc())
        watched = {v.get("channel_id") for v in b["channels"].values() if v.get("channel_id")}
        min_s = 60 * float(self.cfg.get("discovery.min_episode_minutes", 12))
        eps = b["episodes"]
        for person in batch:
            if self.time_left() < 150:
                break
            if self.direct_blocked:
                results = self.mirrors.search_videos(f"{person.name} podcast", 6)
            else:
                try:
                    results = yt.search(f"{person.name} podcast", 6, by_date=True)
                except yt.Blocked:
                    self.direct_blocked = True
                    results = self.mirrors.search_videos(f"{person.name} podcast", 6)
            for r in results:
                if r["video_id"] in eps or r.get("channel_id") in watched:
                    continue
                if r.get("duration") and r["duration"] < min_s:
                    continue
                if person.name not in [x.name for x in self.people.find(r["title"], allow_last_name=True)]:
                    continue
                if r.get("timestamp") and hours_since(iso(datetime.fromtimestamp(r["timestamp"], UTC))) > 48:
                    continue
                eps[r["video_id"]] = {"id": r["video_id"], "title": r["title"], "show": r.get("channel") or "YouTube",
                                      "cat": "watchlist", "podcast_tier": 3, "guest": person.name,
                                      "guest_tier": person.tier, "published": "", "views": r.get("views") or 0,
                                      "stage": "new", "source": f"watchlist:{person.name}",
                                      "found": iso(now_utc()), "updated": iso(now_utc())}
                self.note(f"Watchlist hit: {person.name} on {r.get('channel')} - {r['title'][:60]}")

    # ------------------------------------------------------------- analysis
    def _hot(self, e: dict) -> float:
        boost = float(self.brain.get("playbook", {}).get("person_boost", {}).get(e.get("guest") or "", 1.0))
        age = hours_since(e.get("published") or e.get("found"))
        return episode_hotness(e.get("podcast_tier", 3), e.get("guest_tier", 9), e.get("views", 0),
                               min(age, 48), boost)

    def process(self) -> None:
        eps = self.brain["episodes"]
        todo = []
        for e in eps.values():
            st = e.get("stage")
            if st == "new" or (st == "needs_local" and self.mode != "cloud"):
                todo.append(e)
            elif st == "no_transcript" and hours_since(e.get("updated")) >= 0.5 and e.get("tries", 0) < 8:
                todo.append(e)
            elif st == "done" and e.get("_rescore"):
                todo.append(e)
            elif st == "done":
                age = hours_since(e.get("published") or e.get("found"))
                due = [h for h in RESCORE_AT_H if age >= h and h not in e.get("rescored", [])]
                if due and age < 30:
                    e["_rescore"] = due
                    todo.append(e)
            elif st == "needs_local" and self.mode == "cloud" and hours_since(e.get("updated")) >= 3:
                todo.append(e)  # cloud retries occasionally in case YouTube stops blocking
        todo.sort(key=lambda e: (not e.get("manual") or e.get("stage") == "done" and not e.get("_rescore"),
                                 e.get("stage") != "new", -self._hot(e)))
        limit = int(self.cfg.get("discovery.max_new_episodes_per_run", 6)) * (3 if self.mode != "cloud" else 1)
        fails_in_a_row = 0
        for e in todo[:limit]:
            if self.time_left() < 90 or self.yt_blocked:
                break
            try:
                self.analyse(e)
                fails_in_a_row = 0
            except yt.Blocked:
                # one stubborn video goes to the laptop; only stop if several fail in a row
                e["stage"] = "needs_local"
                e["updated"] = iso(now_utc())
                fails_in_a_row += 1
                self.note(f"Could not read {e['show']} - {e['title'][:50]} here; laptop will finish it")
                if fails_in_a_row >= 3:
                    for rest in todo[todo.index(e) + 1:]:
                        if rest.get("stage") == "new":
                            rest["stage"], rest["updated"] = "needs_local", iso(now_utc())
                    raise
                continue
            except yt.Unreachable:
                self.note("Network problem while reading an episode - will retry next run")
                break
            except Exception as ex:  # noqa: BLE001
                e["tries"] = e.get("tries", 0) + 1
                e["error"] = str(ex)[:200]
                e["updated"] = iso(now_utc())
                if e["tries"] >= 4:
                    e["stage"] = "skipped"
                log.warning("Episode %s failed: %s", e["id"], ex)
            finally:
                e.pop("_rescore", None) if e.get("stage") != "done" else None

    def fetch_video(self, vid: str) -> tuple[dict, list[dict], str]:
        """YouTube directly; if YouTube blocks this machine, free Invidious/Piped mirrors."""
        info, segs, src = None, None, ""
        if not self.direct_blocked:
            try:
                info = yt.video_info(vid, with_comments=True)
                segs, src = yt.transcript(self.http, vid, info)
            except yt.Blocked:
                self.direct_blocked = True
                segs = None
        if info is None or segs is None:
            alt = self.mirrors.video(vid)
            if not alt and self.mirrors.last_reason == "nocaps":
                return info or dict(self.mirrors.last_info or {"id": vid}, comments=[]), [], "none"
            if not alt:
                raise yt.Blocked("YouTube and all free mirrors refused")
            ainfo, segs = alt
            if info is None:
                info = ainfo
            elif not info.get("comments"):
                info["comments"] = ainfo.get("comments", [])
            host = re.sub(r"^https?://", "", ainfo.get("via", "mirror"))
            src = f"mirror:{host}"
            self.via[host] = self.via.get(host, 0) + 1
        return info, segs, src

    def analyse(self, e: dict) -> None:
        cfg = self.cfg
        info, segs, src = self.fetch_video(e["id"])
        e["updated"] = iso(now_utc())
        if e.get("manual"):
            e["title"] = info.get("title") or e["title"]
            e["show"] = info.get("channel") or e["show"]
            if not e.get("guest"):
                e["guest"], e["guest_tier"] = guest_from_title(e["title"], e["show"], self.people)
        if info.get("live_status") in ("is_live", "is_upcoming"):
            e["stage"] = "new"  # premiere / live stream not finished yet - try again next run
            return
        dur = float(info.get("duration") or 0)
        ts = info.get("timestamp") or info.get("release_timestamp")
        if ts:
            e["published"] = iso(datetime.fromtimestamp(ts, UTC))
        if dur and dur < 60 * float(cfg.get("discovery.min_episode_minutes", 12)) and not e.get("manual"):
            e["stage"] = "skipped"
            e["skip_reason"] = "too short"
            return
        if hours_since(e.get("published")) > float(cfg.get("discovery.max_episode_age_hours", 48)) + 24 \
                and not e.get("manual"):
            e["stage"] = "skipped"
            e["skip_reason"] = "too old"
            return
        e.update({"duration": dur, "views": info.get("view_count") or e.get("views", 0),
                  "comments_n": len(info.get("comments") or []), "thumb": info.get("thumbnail") or "",
                  "has_heatmap": bool(info.get("heatmap"))})
        if (info.get("age_limit") or 0) >= 18:
            e["age_restricted"] = True
        if not segs:
            e["stage"] = "no_transcript"
            e["tries"] = e.get("tries", 0) + 1
            return
        e["transcript_source"] = src
        lo, hi = cfg.get("clips.target_seconds", [8, 22])
        moments = score_episode(
            segs, info, self.people, e["show"], lo=float(lo), hi=float(hi),
            hard_max=float(cfg.get("clips.hard_max_seconds", 35)), min_gap=float(cfg.get("clips.min_gap_seconds", 75)),
            top_n=int(cfg.get("ai.candidates_sent_per_episode", 8)), min_score=float(cfg.get("clips.min_score", 45)) * 0.8,
            playbook=self.brain.get("playbook"))
        self.make_clips(e, moments, segs)
        e["stage"] = "done"
        e["analysed"] = iso(now_utc())
        e["rescored"] = sorted(set(e.get("rescored", []) + [h for h in e.pop("_rescore", []) if h != "manual"]))
        if not e["rescored"]:
            age = hours_since(e.get("published") or e.get("found"))
            e["rescored"] = [h for h in RESCORE_AT_H if age >= h]
        self.note(f"Analysed {e['show']}: {len(moments)} moments ({src}, {e['comments_n']} comments)")

    def make_clips(self, e: dict, moments: list[Moment], segs: list[dict]) -> None:
        clips = self.brain["clips"]
        who = e.get("guest")
        cands = []
        for m in moments:
            cid = short_id(e["id"], int(m.start // 6))
            before = " ".join(s["text"] for s in segs if m.start - 25 <= s["start"] < m.start)
            after = " ".join(s["text"] for s in segs if m.end <= s["start"] < m.end + 12)
            speaker = who
            lines = [[round(max(0.0, s["start"] - m.start), 2), round(min(m.end, s["start"] + s["dur"]) - m.start, 2),
                      s["text"]] for s in segs if s["start"] + s["dur"] > m.start and s["start"] < m.end]
            cands.append({"id": cid, "who": speaker, "text": m.text, "before": before, "after": after, "m": m,
                          "lines": lines})
        verdicts = self.ai.judge({"show": e["show"], "title": e["title"], "guest": who},
                                 cands, self.brain.get("playbook", {}).get("style_examples", []))
        hot = self._hot(e)
        min_score = float(self.cfg.get("clips.min_score", 45))
        keep = int(self.cfg.get("clips.per_episode", 5))
        built = []
        for c in cands:
            m: Moment = c["m"]
            v = verdicts.get(c["id"])
            score = m.score
            cap = ""
            hook = ""
            why = ""
            if v:
                score = 0.6 * m.score + 0.4 * v["viral"] * 10 - (15 if v["standalone"] < 4 else 0)
                cap = clean_caption(v.get("caption", ""))
                hook = v.get("hook", "")
                why = v.get("why", "")
            if not caption_ok(cap):
                cap = template_caption(c["who"], m.hits, m.people, m.text, c["lines"])
            hook = (hook or hook_text(cap)).upper()[:40]
            rank = round(0.7 * score + 0.3 * hot, 1)
            old = clips.get(c["id"], {})
            built.append({
                "id": c["id"], "video_id": e["id"], "show": e["show"], "cat": e.get("cat"), "episode": e["title"],
                "guest": who, "start": round(m.start, 1), "end": round(m.end, 1), "duration": round(m.duration, 1),
                "quote": m.text, "caption": cap, "hook": hook, "score": round(score, 1), "rule_score": m.score,
                "ai": v or None, "why": why, "reasons": m.reasons[:6], "people": m.people, "rank": rank,
                "hot": hot, "url": f"https://youtu.be/{e['id']}?t={int(m.start)}", "at": fmt_ts(m.start),
                "thumb": f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg", "published": e.get("published"),
                "found": old.get("found") or iso(now_utc()), "updated": iso(now_utc()),
                "age_restricted": e.get("age_restricted", False), "lines": c["lines"],
            })
        built.sort(key=lambda x: -x["score"])
        if e.get("manual"):  # you asked for this video: always show its best moments
            min_score *= 0.5
            for c in built:
                c["manual"] = True
        for c in built[:keep]:
            if c["score"] >= min_score:
                clips[c["id"]] = c

    # ------------------------------------------------------------------ study
    def study_due(self) -> bool:
        if not self.cfg.x_accounts:
            return False
        xs = self.brain["xstudy"]
        if self.mode != "cloud" and "blocked" in str((xs.get("health") or {}).get("sources", {})) \
                and hours_since(xs.get("laptop_run")) >= 1:
            xs["laptop_run"] = iso(now_utc())
            return True
        return hours_since(xs.get("last_run")) >= float(self.cfg.get("x_study.every_hours", 3))

    # ---------------------------------------------------------------- publish
    def prune(self) -> None:
        b = self.brain
        b["episodes"] = {k: v for k, v in b["episodes"].items() if hours_since(v.get("found")) < 24 * 7}
        b["clips"] = {k: v for k, v in b["clips"].items() if hours_since(v.get("found")) < 24 * 5}

    def batches(self, clips: list[dict], status: dict) -> list[dict]:
        out = []
        used: set[str] = set()
        now = datetime.now(IST)
        occ = []
        for bt in self.cfg.get("posting.batches", []):
            hh, mm = map(int, str(bt.get("time", "07:00")).split(":"))
            t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if t < now - timedelta(minutes=45):
                t += timedelta(days=1)
            occ.append((t, bt))
        occ.sort(key=lambda x: x[0])
        for t, bt in occ:
            pool = [c for c in clips if c["id"] not in used and status.get(c["id"], {}).get("status") not in ("posted", "skipped")
                    and hours_since(c["found"], t.astimezone(UTC)) <= 30 and parse_iso(c["found"]) <= t.astimezone(UTC)]
            pool.sort(key=lambda c: -self.fresh_rank(c, t))
            pick = []
            per_show: dict[str, int] = {}
            for c in pool:
                if per_show.get(c["video_id"], 0) >= 2:  # max 2 clips from one episode per batch
                    continue
                pick.append(c["id"])
                per_show[c["video_id"]] = per_show.get(c["video_id"], 0) + 1
                if len(pick) >= int(bt.get("clips", 5)):
                    break
            used |= set(pick)
            out.append({"name": bt.get("name"), "time_ist": t.strftime("%a %d %b, %I:%M %p"), "at": iso(t),
                        "prep_at": iso(t - timedelta(minutes=30)), "clips": pick})
        return out

    @staticmethod
    def fresh_rank(c: dict, t: datetime) -> float:
        age = max(0.0, hours_since(c.get("published") or c["found"], t.astimezone(UTC)))
        return c["rank"] * math.exp(-age / 20.0)

    def build_app(self) -> dict:
        b = self.brain
        status = (self.feedback or {}).get("clips", {})
        clips = sorted(b["clips"].values(), key=lambda c: -c["rank"])
        for c in clips:
            c["status"] = status.get(c["id"], {}).get("status", "")
        eps = sorted(b["episodes"].values(), key=lambda e: e.get("found", ""), reverse=True)[:80]
        pending_local = sum(1 for e in b["episodes"].values() if e.get("stage") == "needs_local")
        ch = b["channels"]
        sources = [{"name": p.name, "ok": bool(ch.get(p.name, {}).get("channel_id")),
                    "title": ch.get(p.name, {}).get("title", ""), "how": ch.get(p.name, {}).get("method", "pending")}
                   for p in self.cfg.podcasts]
        pb = b.get("playbook", {})
        xs = b.get("xstudy", {})
        health = {
            "updated": iso(now_utc()), "mode": self.mode, "youtube_blocked": self.yt_blocked,
            "direct_blocked": self.direct_blocked, "via": self.via, "last_manual": b.get("last_manual"),
            "pending_for_laptop": pending_local, "sources_found": sum(s["ok"] for s in sources),
            "sources_total": len(sources), "ai": {"available": self.ai.available, "tokens_today": b["ai_usage"].get("tokens", 0),
                                                  "exhausted": b["ai_usage"].get("exhausted", []),
                                                  "errors": b["ai_usage"].get("errors", {})},
            "x_study": {"tracked": len(xs.get("posts", {})), "last_run": xs.get("last_run"), **(xs.get("health") or {})},
            "events": (b.get("health", {}).get("events", []) + self.events)[-40:],
        }
        return {
            "v": 1, "generated": iso(now_utc()),
            "batches": self.batches(clips, status),
            "clips": clips[:160],
            "episodes": [{k: e.get(k) for k in ("id", "title", "show", "cat", "guest", "published", "views", "stage",
                                                "comments_n", "found", "duration", "source", "skip_reason")} for e in eps],
            "insights": {"n_posts": pb.get("n_posts", 0), "lines": pb.get("insights", []),
                         "people": sorted(pb.get("person_boost", {}).items(), key=lambda kv: -kv[1])[:15],
                         "topics": sorted(pb.get("topic_boost", {}).items(), key=lambda kv: -kv[1])[:15],
                         "examples": pb.get("style_examples", [])[:10], "accounts": pb.get("accounts", {}),
                         "trending": pb.get("trending_people", [])},
            "sources": sources, "health": health,
            "settings": {"dead_window_ist": self.cfg.get("posting.dead_window_ist", [])},
        }
