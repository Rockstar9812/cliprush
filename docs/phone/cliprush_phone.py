"""ClipRush phone cutter - runs inside the free iPhone app a-Shell mini.

The ClipRush app's Download button starts the "ClipRush Cut" Shortcut, which runs:
    python3 cliprush_phone.py <clip-code>
It reads ONLY the clip's seconds straight from YouTube (your phone is never blocked),
burns the captions at the bottom, encodes H.264 with the iPhone's hardware encoder and
writes ~/Documents/ClipRush/latest.mp4 - the Shortcut then saves it to Photos.

Manual use:  python3 cliprush_phone.py VIDEO_ID START_SECONDS END_SECONDS
"""
import base64
import json
import os
import subprocess
import sys
import urllib.request

PAGES = "https://rockstar9812.github.io/cliprush"
HOME = os.path.expanduser("~/Documents/ClipRush")
FONT_NAME = "DejaVuSans-Bold.ttf"
FMT = ("bv*[vcodec^=avc1][height<=1080]+ba[acodec^=mp4a]/bv*[vcodec^=avc1][height<=1080]+ba/"
       "bv*[height<=1080]+ba/b[height<=1080]/b")


def log(msg):
    print(msg, flush=True)


def parse_args(argv):
    if len(argv) >= 4:
        return {"v": argv[1], "s": float(argv[2]), "e": float(argv[3]), "h": "", "l": []}
    if len(argv) == 2:
        code = argv[1].strip()
        if code.startswith("clip "):
            code = code[5:]
        code += "=" * (-len(code) % 4)
        return json.loads(base64.urlsafe_b64decode(code.encode()).decode("utf-8"))
    raise SystemExit("usage: python3 cliprush_phone.py <clip-code>  |  VIDEO_ID START END")


# ------------------------------------------------------------------ captions
def _t(x):
    x = max(0.0, x)
    h, r = divmod(x, 3600)
    m, s = divmod(r, 60)
    return "%d:%02d:%05.2f" % (h, m, s)


def _esc(s):
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _chunks(a, b, text, max_words=7):
    words = text.split()
    if not words:
        return []
    n = max(1, -(-len(words) // max_words))
    per = -(-len(words) // n)
    out, t = [], a
    for i in range(0, len(words), per):
        piece = words[i:i + per]
        d = (b - a) * len(piece) / len(words)
        out.append((t, t + d, " ".join(piece)))
        t += d
    return out


def make_ass(clip, dur, w, h, hook_s=3.0):
    fs, hs = int(h * 0.055), int(h * 0.060)
    by, ty = int(h * 0.94), int(h * 0.06)
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0",
             "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
             "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
             "MarginR, MarginV, Encoding",
             f"Style: Cap,DejaVu Sans,{fs},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,"
             f"{max(3, fs // 11)},2,2,90,90,{h - by},1",
             f"Style: Hook,DejaVu Sans,{hs},&H0000FFFF,&H000000FF,&H00000000,&HB4000000,-1,0,0,0,100,100,0,0,3,"
             f"{max(4, hs // 10)},0,8,90,90,{ty},1", "", "[Events]",
             "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    if clip.get("h"):
        lines.append(f"Dialogue: 1,{_t(0)},{_t(min(hook_s, dur))},Hook,,0,0,0,,{{\\an8\\pos({w // 2},{ty})}}{_esc(clip['h'])}")
    for a, b, text in clip.get("l") or []:
        a, b = float(a), min(float(b), dur)
        if b > a and text.strip():
            for ca, cb, piece in _chunks(a, b, _esc(text)):
                lines.append(f"Dialogue: 0,{_t(ca)},{_t(cb)},Cap,,0,0,0,,{{\\an2\\pos({w // 2},{by})}}{piece}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ helpers
def ensure_font():
    fdir = os.path.join(HOME, "fonts")
    os.makedirs(fdir, exist_ok=True)
    path = os.path.join(fdir, FONT_NAME)
    if not os.path.exists(path) or os.path.getsize(path) < 10000:
        try:
            urllib.request.urlretrieve(f"{PAGES}/phone/{FONT_NAME}", path)
        except Exception as e:  # noqa: BLE001
            log(f"(font download failed: {e} - captions may not show)")
    return fdir


def stream_sources(vid):
    """Direct stream URLs for the best H.264 video + audio, chosen by yt-dlp (no download)."""
    test = os.environ.get("CLIPRUSH_TEST_SRC")  # used by automated tests only
    if test:
        return [{"url": test, "headers": {}}], 1080
    import yt_dlp

    class Quiet:
        def debug(self, m): pass
        def warning(self, m): pass
        def error(self, m): log(m)
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "format": FMT, "logger": Quiet(),
                           "noplaylist": True}) as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
    fmts = info.get("requested_formats") or [info]
    out = [{"url": f["url"], "headers": f.get("http_headers") or info.get("http_headers") or {}} for f in fmts]
    height = max([f.get("height") or 0 for f in fmts] + [0]) or 720
    return out, height


def run_ffmpeg(cmd, cwd):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode == 0, (p.stderr or "")[-600:]


def main():
    clip = parse_args(sys.argv)
    vid, start, end = clip["v"], max(0.0, float(clip["s"])), float(clip["e"])
    dur = max(1.0, end - start)
    os.makedirs(HOME, exist_ok=True)
    ensure_font()
    log(f"Getting {vid} {start:.0f}s-{end:.0f}s ...")
    srcs, src_h = stream_sources(vid)
    h = 1080 if src_h >= 1000 else 720
    w = h * 16 // 9
    with open(os.path.join(HOME, "cap.ass"), "w", encoding="utf-8") as f:
        f.write(make_ass(clip, dur, w, h))
    out = os.path.join(HOME, "latest.mp4")
    inputs = []
    for s in srcs:
        hdr = "".join(f"{k}: {v}\r\n" for k, v in s["headers"].items() if k.lower() != "user-agent")
        ua = next((v for k, v in s["headers"].items() if k.lower() == "user-agent"), None)
        if ua:
            inputs += ["-user_agent", ua]
        if hdr:
            inputs += ["-headers", hdr]
        inputs += ["-ss", f"{start:.3f}", "-i", s["url"]]
    amap = ["-map", "0:v:0", "-map", ("1:a:0" if len(srcs) > 1 else "0:a:0?")]
    base_vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
               f"setsar=1")
    tries = [
        (base_vf + ",subtitles=cap.ass:fontsdir=fonts", ["-c:v", "h264_videotoolbox", "-b:v", "8M", "-allow_sw", "1"]),
        (base_vf + ",subtitles=cap.ass:fontsdir=fonts", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]),
        (base_vf, ["-c:v", "h264_videotoolbox", "-b:v", "8M", "-allow_sw", "1"]),
        (base_vf, ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]),
    ]
    err = ""
    for i, (vf, venc) in enumerate(tries):
        cmd = (["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + inputs + ["-t", f"{dur:.3f}"] + amap +
               ["-vf", vf] + venc + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out])
        ok, err = run_ffmpeg(cmd, HOME)
        if ok and os.path.exists(out) and os.path.getsize(out) > 20000:
            if i >= 2:
                log("Saved WITHOUT burned captions (caption step failed) - the caption text is still in the app.")
            log(f"OK {out} ({os.path.getsize(out) // 1024} KB, {dur:.0f}s)")
            return 0
    log("FAILED: " + err.strip().splitlines()[-1] if err.strip() else "FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
