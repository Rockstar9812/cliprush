"""Loads settings.yaml + sources.yaml with safe defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
def config_dir() -> Path:
    return Path(os.environ.get("CLIPRUSH_CONFIG", ROOT / "config"))


def _load(name: str) -> dict:
    p = config_dir() / name
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get(d: dict, path: str, default: Any) -> Any:
    cur: Any = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


@dataclass
class Person:
    name: str
    tier: int = 2
    aka: list[str] = field(default_factory=list)

    @property
    def all_names(self) -> list[str]:
        return [self.name] + list(self.aka)


@dataclass
class Podcast:
    name: str
    handle: str = ""
    tier: int = 2
    cat: str = "other"
    channel_id: str = ""


class Config:
    def __init__(self) -> None:
        self.settings = _load("settings.yaml")
        src = _load("sources.yaml")
        self.podcasts = [Podcast(**{k: v for k, v in p.items() if k in Podcast.__annotations__})
                         for p in src.get("podcasts", []) if p.get("name")]
        self.people = [Person(name=p["name"], tier=int(p.get("tier", 2)), aka=list(p.get("aka", []) or []))
                       for p in src.get("people", []) if p.get("name")]
        self.x_accounts = [str(a).lstrip("@").strip() for a in src.get("x_accounts", []) if str(a).strip()]
        self.x_search_terms = [str(t) for t in src.get("x_search_terms", [])]

    def get(self, path: str, default: Any = None) -> Any:
        return _get(self.settings, path, default)
