"""ClipRush phone cutter for the free iPhone app a-Shell.

The ClipRush app's Approve button starts an iOS Shortcut that runs:
    python3 cliprush_phone.py <video_id> <start_seconds> <end_seconds> <name>
It downloads ONLY those seconds and saves ~/Documents/ClipRush/<name>.mp4,
which the Shortcut then saves to Photos. Nothing is downloaded until you approve.

One-time setup inside a-Shell:   pip install yt-dlp
"""
import os
import sys


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: python3 cliprush_phone.py VIDEO_ID START END [NAME]")
        return 2
    vid, start, end = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    name = (sys.argv[4] if len(sys.argv) > 4 else f"{vid}_{int(start)}").replace("/", "-")
    out_dir = os.path.expanduser("~/Documents/ClipRush")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, name + ".mp4")
    for f in os.listdir(out_dir):  # keep the phone tidy: only the newest clip stays
        if f.endswith(".mp4") and f != os.path.basename(out):
            try:
                os.remove(os.path.join(out_dir, f))
            except OSError:
                pass
    script = os.path.join(out_dir, "cut.sh")
    with open(script, "w") as f:  # the Shortcut always runs this; it is a no-op unless plan B needs it
        f.write("echo ok\n")
    import yt_dlp
    url = f"https://www.youtube.com/watch?v={vid}"
    # Plan A: yt-dlp cuts the section itself (needs ffmpeg, which a-Shell ships).
    try:
        from yt_dlp.utils import download_range_func
        opts = {"quiet": True, "outtmpl": out, "format": "b[ext=mp4][height<=720]/b[ext=mp4]/b",
                "download_ranges": download_range_func(None, [(max(0.0, start), end)]),
                "force_keyframes_at_cuts": True, "noplaylist": True}
        with yt_dlp.YoutubeDL(opts) as y:
            y.download([url])
        if os.path.exists(out) and os.path.getsize(out) > 10_000:
            print(out)
            return 0
    except Exception as e:  # noqa: BLE001
        print("plan A failed:", e, file=sys.stderr)
    # Plan B: get the direct stream URL and write an ffmpeg command for a-Shell to run next.
    with yt_dlp.YoutubeDL({"quiet": True, "format": "b[ext=mp4][height<=720]/b[ext=mp4]/b"}) as y:
        info = y.extract_info(url, download=False)
    stream = info.get("url")
    if not stream:
        print("no progressive stream found", file=sys.stderr)
        return 1
    cmd = f'ffmpeg -y -ss {max(0.0, start):.2f} -i "{stream}" -t {end - start:.2f} -c:v libx264 -preset veryfast -c:a aac "{out}"'
    with open(script, "w") as f:
        f.write(cmd + "\n")
    print("RUN_NEXT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
