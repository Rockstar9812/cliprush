"""One-click setup: finds your notes file with the keys, writes cliprush.env, checks every key,
and makes a phone-setup page (QR code) plus a one-click link for the laptop app.
Your keys never leave this laptop (except to the services they belong to, when checked)."""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENV = HERE / "cliprush.env"
DEFAULT_REPO = "Rockstar9812/cliprush"
APP_URL = "https://rockstar9812.github.io/cliprush/"

PATTERNS = {
    "GROQ_API_KEY": r"gsk_[A-Za-z0-9]{20,}",
    "GEMINI_API_KEY": r"(?:AIza[0-9A-Za-z_\-]{30,}|AQ\.[0-9A-Za-z_\-.]{20,})",
    "APIFY_TOKEN": r"apify_api_[A-Za-z0-9]{20,}",
    "CLIPRUSH_GITHUB_TOKEN": r"(?:github_pat_[A-Za-z0-9_]{40,}|ghp_[A-Za-z0-9]{30,})",
}


def find_notes() -> Path | None:
    home = Path.home()
    places = [ROOT, ROOT.parent, ROOT.parent.parent, home / "Downloads", home / "Desktop", home / "Documents",
              home / "OneDrive" / "Desktop", home / "OneDrive" / "Documents"]
    cands = []
    for d in places:
        if not d.exists():
            continue
        for p in list(d.glob("*.txt")) + list(d.glob("*/*.txt")) + list(d.glob("*/*/*.txt")):
            n = p.name.lower()
            if "clip" in n and ("pass" in n or "key" in n or "secret" in n):
                cands.append(p)
            elif "password" in n or "keys" in n:
                try:
                    if "gsk_" in p.read_text(errors="ignore") or "github_pat_" in p.read_text(errors="ignore"):
                        cands.append(p)
                except OSError:
                    pass
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def parse(text: str) -> dict:
    out = {}
    for k, rx in PATTERNS.items():
        m = re.search(rx, text)
        if m:
            out[k] = m.group(0).strip().rstrip(".")
    m = re.search(r"password\s*[:=]\s*(\S+)", text, re.I)
    if not m:
        m = re.search(r"CLIPRUSH_PASSWORD\s*[:=]?\s*\n?\s*(\S+)", text)
    if m:
        out["CLIPRUSH_PASSWORD"] = m.group(1).strip()
    m = re.search(r"\b([A-Za-z0-9-]+/cliprush)\b", text)
    out["CLIPRUSH_REPO"] = m.group(1) if m else DEFAULT_REPO
    return out


def read_env() -> dict:
    d = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                if v.strip():
                    d[k.strip()] = v.strip()
    return d


def write_env(v: dict) -> None:
    order = ["CLIPRUSH_REPO", "CLIPRUSH_GITHUB_TOKEN", "CLIPRUSH_PASSWORD", "GROQ_API_KEY", "GEMINI_API_KEY",
             "APIFY_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "YT_COOKIES"]
    lines = ["# Written by easy_setup.py - private, stays on this laptop."]
    lines += [f"{k}={v.get(k, '')}" for k in order]
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def check(v: dict) -> list[str]:
    import requests
    res = []

    def line(ok, name, extra=""):
        res.append(f"  [{'OK ' if ok else 'BAD'}] {name}{(' - ' + extra) if extra else ''}")
    t = v.get("CLIPRUSH_GITHUB_TOKEN", "")
    if t:
        r = requests.get(f"https://api.github.com/repos/{v['CLIPRUSH_REPO']}", timeout=20,
                         headers={"Authorization": f"Bearer {t}", "Accept": "application/vnd.github+json"})
        line(r.status_code == 200, "GitHub token + repo", "" if r.ok else f"{r.status_code}: re-check the token")
    else:
        line(False, "GitHub token", "not found in your notes")
    if v.get("GROQ_API_KEY"):
        r = requests.get("https://api.groq.com/openai/v1/models", timeout=20,
                         headers={"Authorization": f"Bearer {v['GROQ_API_KEY']}"})
        line(r.ok, "Groq key", "" if r.ok else f"{r.status_code} (a letter may be mistyped)")
    if v.get("GEMINI_API_KEY"):
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", timeout=20,
                         params={"key": v["GEMINI_API_KEY"]})
        line(r.ok, "Gemini key", "" if r.ok else f"{r.status_code} (optional - Groq is enough)")
    if v.get("APIFY_TOKEN"):
        r = requests.get("https://api.apify.com/v2/users/me", timeout=20, params={"token": v["APIFY_TOKEN"]})
        line(r.ok, "Apify token", "" if r.ok else f"{r.status_code} (optional)")
    if v.get("CLIPRUSH_PASSWORD"):
        os.environ.update({k: val for k, val in v.items() if val})
        sys.path.insert(0, str(ROOT))
        try:
            from brain.store import GitHubStore, load
            load(GitHubStore(v["CLIPRUSH_REPO"], t), v["CLIPRUSH_PASSWORD"])
            line(True, "Password matches your cloud data")
        except Exception as e:  # noqa: BLE001
            msg = "wrong password (must equal the CLIPRUSH_PASSWORD secret on GitHub)" if "InvalidTag" in type(e).__name__ \
                else str(e)[:80]
            line(False, "Password", msg)
    else:
        line(False, "Password", "not found in your notes (write: Password : yourpassword)")
    return res


def setup_link(v: dict) -> str:
    payload = {"r": v["CLIPRUSH_REPO"], "p": v.get("CLIPRUSH_PASSWORD", ""), "t": v.get("CLIPRUSH_GITHUB_TOKEN", "")}
    b = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    owner, name = v["CLIPRUSH_REPO"].split("/")
    return f"https://{owner.lower()}.github.io/{name}/#setup={b}"


def phone_page(link: str) -> Path:
    svg = ""
    try:
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage, box_size=12)
        svg = img.to_string(encoding="unicode")
    except Exception:  # noqa: BLE001
        pass
    page = HERE / "phone_setup.html"
    page.write_text(f"""<!doctype html><meta charset="utf-8"><title>ClipRush phone setup</title>
<body style="font-family:Segoe UI,sans-serif;background:#0b0d10;color:#eee;text-align:center;padding:30px">
<h1>Set up ClipRush on your iPhone</h1>
<p>1. Open the iPhone <b>Camera</b> and point it at this code. Tap the link (it opens in Safari).</p>
<div style="background:#fff;display:inline-block;padding:16px;border-radius:12px">{svg or '<p style="color:#000">QR code unavailable - use the Copy link button below</p>'}</div>
<p style="margin-top:18px">No camera? Send yourself this link (e.g. WhatsApp to yourself / AirDrop) and open it on the iPhone:</p>
<p><input id="l" value="{link}" readonly style="width:80%;padding:10px;border-radius:8px;border:0">
<button onclick="navigator.clipboard.writeText(document.getElementById('l').value);this.textContent='Copied'"
 style="padding:10px 16px;border-radius:8px;border:0;background:#ff4d2e;color:#fff;font-weight:700">Copy link</button></p>
<p>2. In Safari: <b>Share &rarr; Add to Home Screen</b>. Done - no password typing.</p>
<p style="color:#f88">Private: this code contains your password and GitHub token. Don't share or screenshot it.</p>
</body>""", encoding="utf-8")
    return page


def main() -> int:
    print("\n=== ClipRush easy setup ===\n")
    vals = read_env()
    notes = find_notes()
    if notes:
        print(f"Found your notes: {notes}")
        vals.update({k: v for k, v in parse(notes.read_text(encoding="utf-8", errors="ignore")).items() if v})
    else:
        print("Could not find your notes file (a .txt with 'clip' and 'password' in its name).")
        print("Paste everything from your notes here, then press Enter twice:")
        buf = []
        while True:
            try:
                ln = input()
            except EOFError:
                break
            if not ln and buf and not buf[-1]:
                break
            buf.append(ln)
        vals.update({k: v for k, v in parse("\n".join(buf)).items() if v})
    vals.setdefault("CLIPRUSH_REPO", DEFAULT_REPO)
    write_env(vals)
    print(f"Saved settings to {ENV}\n\nChecking your keys...")
    for ln in check(vals):
        print(ln)
    link = setup_link(vals)
    page = phone_page(link)
    (HERE / "app_link.txt").write_text(link, encoding="utf-8")
    print("\nOpening the app (already unlocked) and the phone setup page...")
    if hasattr(os, "startfile"):
        os.startfile(link)  # noqa: S606
        time.sleep(1.5)
        os.startfile(str(page))  # noqa: S606
    return 0


if __name__ == "__main__":
    sys.exit(main())
