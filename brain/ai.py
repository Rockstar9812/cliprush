"""Optional free AI layer (Groq free plan, Gemini free tier). Never required:
if no key is set, a provider is out of quota, or the reply is malformed, ClipRush
falls back to its rule engine and template captions."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Optional

from .util import IST, Http, log

SYSTEM = """You are the editor of a viral X (Twitter) clip account that reposts the most controversial,
shocking and curious moments from celebrity, athlete, streamer and creator podcasts. You judge moments the way
a 1M-view clip account would. You never invent facts: captions must be supported by the quote.

Caption rules (from measured data): 8-12 words. No emoji. No hashtags. Third person. Name the speaker
or the person being talked about. Present tense. Create curiosity; a short direct quote fragment is good.
Hook: max 5 words, punchy, for the first 3 seconds on screen.

Reply with JSON only: {"moments":[{"id":"...","viral":0-10,"standalone":0-10,"why":"<12 words",
"caption":"...","hook":"..."}]}. viral = how likely to explode on X. standalone = makes sense with no context."""


def _est_tokens(s: str) -> int:
    return max(1, len(s) // 4)


class AI:
    def __init__(self, http: Http, cfg, usage: dict):
        self.http = http
        self.cfg = cfg
        self.usage = usage  # persisted dict: {"date": "YYYY-MM-DD", "tokens": int, "calls": {...}, "errors": {...}}
        self.keys = {"groq": os.environ.get("GROQ_API_KEY", "").strip(),
                     "gemini": os.environ.get("GEMINI_API_KEY", "").strip()}
        today = datetime.now(IST).strftime("%Y-%m-%d")
        if self.usage.get("date") != today:
            self.usage.clear()
            self.usage.update({"date": today, "tokens": 0, "calls": {}, "errors": {}})
        self.disabled_today: set[str] = set(self.usage.get("exhausted", []))

    @property
    def available(self) -> bool:
        return any(self.keys[p] and p not in self.disabled_today for p in self.keys)

    def _budget_ok(self, need: int) -> bool:
        return self.usage.get("tokens", 0) + need <= int(self.cfg.get("ai.daily_token_budget", 180000))

    def _mark(self, provider: str, tokens: int = 0, error: Optional[str] = None) -> None:
        self.usage["tokens"] = self.usage.get("tokens", 0) + tokens
        self.usage.setdefault("calls", {})[provider] = self.usage["calls"].get(provider, 0) + 1
        if error:
            self.usage.setdefault("errors", {})[provider] = error[:160]

    def judge(self, episode: dict, cands: list[dict], style_examples: list[str]) -> dict[str, dict]:
        """cands: [{id, who, text, before, after}] -> {id: {viral, standalone, why, caption, hook}}"""
        if not cands or not self.available:
            return {}
        ex = "\n".join(f"- {c}" for c in style_examples[:8]) or "- (none yet)"
        body = {
            "episode": {"show": episode.get("show"), "title": episode.get("title"), "guest": episode.get("guest")},
            "moments": [{"id": c["id"], "speaker_guess": c.get("who"), "context_before": c.get("before", "")[-220:],
                         "quote": c["text"][:900], "context_after": c.get("after", "")[:160]} for c in cands],
        }
        user = (f"Real captions from clips that passed 1M views on X (match this style):\n{ex}\n\n"
                f"Judge these moments and write captions:\n{json.dumps(body, ensure_ascii=False)}")
        need = _est_tokens(SYSTEM + user) + 120 * len(cands)
        if not self._budget_ok(need):
            log.info("AI daily budget reached - using rules only")
            return {}
        for provider in self.cfg.get("ai.order", ["groq", "gemini"]):
            if not self.keys.get(provider) or provider in self.disabled_today:
                continue
            try:
                text = self._groq(user) if provider == "groq" else self._gemini(user)
            except Exception as e:  # noqa: BLE001
                self._mark(provider, 0, str(e))
                log.warning("AI %s failed: %s", provider, e)
                continue
            if text is None:
                continue
            self._mark(provider, need)
            parsed = _parse(text)
            if parsed:
                return parsed
            self._mark(provider, 0, "unparseable reply")
        return {}

    def _groq(self, user: str) -> Optional[str]:
        for model in (self.cfg.get("ai.groq_model"), self.cfg.get("ai.groq_fallback_model")):
            if not model:
                continue
            r = self.http.post("https://api.groq.com/openai/v1/chat/completions",
                               headers={"Authorization": f"Bearer {self.keys['groq']}"},
                               json={"model": model, "temperature": 0.3, "max_tokens": 1800,
                                     "response_format": {"type": "json_object"},
                                     "messages": [{"role": "system", "content": SYSTEM},
                                                  {"role": "user", "content": user}]})
            if r is None:
                continue
            if r.status_code == 429:
                if "day" in r.text.lower() or "tpd" in r.text.lower():
                    self._exhaust("groq")
                    return None
                continue
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            if r.status_code in (400, 404):  # model retired/renamed - try fallback model
                log.info("Groq model %s unavailable (%s)", model, r.status_code)
                continue
            raise RuntimeError(f"groq {r.status_code}: {r.text[:120]}")
        return None

    def _gemini(self, user: str) -> Optional[str]:
        model = self.cfg.get("ai.gemini_model", "gemini-flash-latest")
        r = self.http.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                           params={"key": self.keys["gemini"]},
                           json={"systemInstruction": {"parts": [{"text": SYSTEM}]},
                                 "contents": [{"role": "user", "parts": [{"text": user}]}],
                                 "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"}})
        if r is None:
            return None
        if r.status_code == 429:
            self._exhaust("gemini")
            return None
        if r.status_code != 200:
            raise RuntimeError(f"gemini {r.status_code}: {r.text[:120]}")
        j = r.json()
        return "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])

    def _exhaust(self, provider: str) -> None:
        self.disabled_today.add(provider)
        self.usage["exhausted"] = sorted(self.disabled_today)
        log.info("%s free quota used up for today", provider)


def _parse(text: str) -> dict[str, dict]:
    try:
        j = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return {}
        try:
            j = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    out = {}
    for m in (j.get("moments") if isinstance(j, dict) else j) or []:
        if not isinstance(m, dict) or "id" not in m:
            continue
        try:
            out[str(m["id"])] = {"viral": max(0.0, min(10.0, float(m.get("viral", 0)))),
                                 "standalone": max(0.0, min(10.0, float(m.get("standalone", 5)))),
                                 "why": str(m.get("why", ""))[:90], "caption": str(m.get("caption", "")),
                                 "hook": str(m.get("hook", ""))[:40]}
        except (TypeError, ValueError):
            continue
    return out
