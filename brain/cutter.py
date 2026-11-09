"""Laptop cutter: downloads ONLY the clip's seconds (not the whole episode), makes a 16:9
1080p MP4 with burned-in captions and a 3-second hook, and saves it with its caption .txt
into a dated folder. Runs without asking, as you wanted. Approved clips are cut first."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Config
from .store import load, load_feedback, make_store
from .util import IST, log, slug

FFMPEG_GUESSES = [r"C:\ffmpeg\bin\ffmpeg.exe",
                  os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
                  "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"]


def find_ffmpeg() -> Optional[str]:
    p = shutil.which("ffmpeg")
    if p:
        return p
    for g in FFMPEG_GUESSES:
        if g and os.path.exists(g):
            return g
    base = Path(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages"))
    if base.exists():
        for f in base.rglob("ffmpeg.exe"):
            return str(f)
    return None


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _ass_escape(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _wrap(text: str, width: int = 34) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return r"\N".join(lines[-2:]) if len(lines) > 2 else r"\N".join(lines)


def make_ass(clip: dict, hook_seconds: float, w: int = 1920, h: int = 1080) -> str:
    fs = int(h * 0.058)
    hs = int(h * 0.062)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial,{fs},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{max(3, fs // 12)},2,2,80,80,{int(h * 0.07)},1
Style: Hook,Arial,{hs},&H0000FFFF,&H000000FF,&H00000000,&HB4000000,-1,0,0,0,100,100,0,0,3,{max(4, hs // 10)},0,8,80,80,{int(h * 0.06)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    dur = float(clip.get("end", 0)) - float(clip.get("start", 0))
    if clip.get("hook") and hook_seconds > 0:
        ev.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(min(hook_seconds, dur))},Hook,,0,0,0,,{_ass_escape(clip['hook'])}")
    for a, b, t in clip.get("lines") or []:
        if b > a and t.strip():
            ev.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(min(b + 0.15, dur))},Cap,,0,0,0,,{_wrap(_ass_escape(t))}")
    return head + "\n".join(ev) + "\n"


def ffmpeg_render(ffmpeg: str, src: str, dst: str, clip: dict, height: int, burn: bool, hook_s: float) -> None:
    w = int(round(height * 16 / 9 / 2) * 2)
    vf = (f"scale={w}:{height}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
    workdir = os.path.dirname(os.path.abspath(dst))
    ass_name = None
    if burn:
        ass_name = f"_cr_{os.getpid()}.ass"
        with open(os.path.join(workdir, ass_name), "w", encoding="utf-8") as f:
            f.write(make_ass(clip, hook_s, w, height))
        vf += f",subtitles={ass_name}"
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", os.path.basename(dst)]
    try:
        subprocess.run(cmd, check=True, cwd=workdir, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        if burn and "subtitles" in (e.stderr or "").lower():
            log.warning("Subtitle burn failed (%s) - saving without burned captions", e.stderr.strip()[:120])
            return ffmpeg_render(ffmpeg, src, dst, clip, height, False, hook_s)
        raise RuntimeError(e.stderr.strip()[:300]) from e
    finally:
        if ass_name and os.path.exists(os.path.join(workdir, ass_name)):
            os.remove(os.path.join(workdir, ass_name))


def download_section(video_id: str, start: float, end: float, outdir: str, height: int, ffmpeg: str) -> str:
    import yt_dlp
    from yt_dlp.utils import download_range_func
    opts = {"quiet": True, "no_warnings": True, "outtmpl": os.path.join(outdir, "src.%(ext)s"),
            "format": f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/bv*[height<={height}]+ba/b[height<={height}]/b",
            "merge_output_format": "mp4", "download_ranges": download_range_func(None, [(max(0.0, start), end)]),
            "force_keyframes_at_cuts": True, "ffmpeg_location": os.path.dirname(ffmpeg), "noplaylist": True,
            "retries": 3, "fragment_retries": 3}
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([f"https://www.youtube.com/watch?v={video_id}"])
    for f in os.listdir(outdir):
        if f.startswith("src."):
            return os.path.join(outdir, f)
    raise RuntimeError("download produced no file")


def cut_clip(clip: dict, folder: Path, cfg: Config, ffmpeg: str) -> Path:
    height = int(cfg.get("laptop.video_height", 1080))
    day = datetime.now(IST).strftime("%Y-%m-%d")
    out_dir = folder / day
    out_dir.mkdir(parents=True, exist_ok=True)
    who = slug(clip.get("guest") or clip.get("show") or "clip", 24)
    name = f"{int(clip.get('rank', 0)):02d}_{who}_{slug(clip.get('caption', ''), 36)}_{clip['id'][:6]}"
    dst = out_dir / f"{name}.mp4"
    with tempfile.TemporaryDirectory() as td:
        src = download_section(clip["video_id"], clip["start"], clip["end"], td, height, ffmpeg)
        tmp_dst = os.path.join(td, "out.mp4")
        ffmpeg_render(ffmpeg, src, tmp_dst, clip, height, bool(cfg.get("laptop.burn_captions", True)),
                      float(cfg.get("laptop.hook_seconds", 3)))
        shutil.move(tmp_dst, dst)
    (out_dir / f"{name}.txt").write_text(
        f"{clip.get('caption', '')}\n\nSource: {clip.get('show')} - {clip.get('episode')}\n{clip.get('url')}\n"
        f"Score {clip.get('score')} | {'; '.join(clip.get('reasons', [])[:4])}\n", encoding="utf-8")
    return dst


def send_telegram(path: Path, caption: str) -> bool:
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not tok or not chat or path.stat().st_size > 49 * 1024 * 1024:
        return False
    import requests
    with open(path, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendVideo", data={"chat_id": chat, "caption": caption[:1000],
                          "supports_streaming": "true"}, files={"video": f}, timeout=300)
    return r.ok


def run_cutter(store=None, password: Optional[str] = None) -> list[str]:
    cfg = Config()
    store = store or make_store()
    password = password or os.environ.get("CLIPRUSH_PASSWORD", "")
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise SystemExit("ffmpeg not found. Run laptop\\install_windows.bat again.")
    app, _ = load(store, password)
    status = load_feedback(store).get("clips", {})
    folder = Path(os.path.expandvars(os.path.expanduser(cfg.get("laptop.clips_folder", "~/ClipRush"))))
    folder.mkdir(parents=True, exist_ok=True)
    idx_path = folder / "downloaded.json"
    done = json.loads(idx_path.read_text()) if idx_path.exists() else {}
    min_score = float(cfg.get("laptop.min_score_to_download", 55))
    in_batch = {cid for b in app.get("batches", []) for cid in b.get("clips", [])}
    from .util import hours_since
    manual = lambda c: c.get("manual") and hours_since(c.get("found")) < 6  # noqa: E731
    todo = [c for c in app.get("clips", []) if c["id"] not in done and status.get(c["id"], {}).get("status") != "skipped"
            and (c["score"] >= min_score or c["id"] in in_batch or manual(c)
                 or status.get(c["id"], {}).get("status") == "approved")]
    todo.sort(key=lambda c: (not manual(c), status.get(c["id"], {}).get("status") != "approved",
                             c["id"] not in in_batch, -c["rank"]))
    made = []
    for c in todo[: int(cfg.get("laptop.max_downloads_per_run", 12))]:
        try:
            p = cut_clip(c, folder, cfg, ffmpeg)
            done[c["id"]] = {"file": str(p), "at": datetime.now(IST).isoformat()}
            made.append(str(p))
            log.info("Saved %s", p)
            if status.get(c["id"], {}).get("status") == "approved" and send_telegram(p, c.get("caption", "")):
                done[c["id"]]["telegram"] = True
        except Exception as e:  # noqa: BLE001
            log.warning("Cut failed for %s: %s", c["id"], e)
            done.setdefault("_errors", {})[c["id"]] = str(e)[:200]
        idx_path.write_text(json.dumps(done, indent=1))
    return made
