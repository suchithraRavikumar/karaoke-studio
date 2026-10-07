#!/usr/bin/env python3
"""
karaoke.py — turn a timed lyrics file + song audio into a YouTube-ready karaoke MP4.

Features
  * Word-by-word "wipe" highlighting synced to the music
  * Different colours for Male (M), Female (F) and Both/Duet (D) parts,
    including switching singer in the middle of a line
  * The current line plus the NEXT 2 lines are always on screen
  * Title card and a 3-2-1 countdown before the first line
  * Optional background image or looping background video

Requirements: Python 3.8+ and ffmpeg (with libass) on your PATH.

Usage
  python karaoke.py song.lrc --audio song.mp3 -o song_karaoke.mp4
  python karaoke.py song.lrc --audio song.mp3 --bg photo.jpg -o out.mp4
  python karaoke.py song.lrc --audio song.mp3 --bg loop.mp4 -o out.mp4
  python karaoke.py song.lrc --ass-only -o song.ass      # subtitles only

Lyrics file format (see README.md for full details)
  title: Song Title
  artist: Artist Name
  [00:12.50] M: A line the man sings
  [00:16.20] F: A line the woman sings
  [00:20.00] D: A line they sing together
  [00:24.00] M: He starts this line {F} and she finishes it
  [00:28.00]                       <- empty line = previous line ends here
  [00:35.10] F: <00:35.10>Word <00:35.60>level <00:36.40>timing <00:37.00>works <00:37.80>too
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata

# ----------------------------------------------------------------------------
# Look & feel — change these to taste
# ----------------------------------------------------------------------------
W, H, FPS = 1920, 1080, 30
# Nirmala UI ships with Windows and covers Tamil, Hindi, Telugu, Kannada,
# Malayalam, Bengali, Gujarati, Punjabi, Odia and English. Override with --font.
FONT = "Nirmala UI" if sys.platform.startswith("win") else "Noto Sans"
SINGER_COLORS = {              # #RRGGBB
    "M": "#34A8FF",            # male   — blue
    "F": "#FF4FA8",            # female — pink
    "D": "#FFC83D",            # both   — gold
}
SINGER_NAMES = {"M": "Male", "F": "Female", "D": "Both"}
UNSUNG = "#FFFFFF"             # colour of words not yet sung
CUR_SIZE, NEXT_SIZE = 78, 58   # font sizes: current line, upcoming lines
SLOT_Y = [560, 700, 810]       # vertical centre of: current, next, next+1
PREVIEW_ALPHA = "20"           # hex transparency for upcoming lines (00 opaque .. FF invisible)
MAX_TEXT_W = 1720              # shrink long lines to fit this width (px)
DEFAULT_BG = "#12122A"

# Lyric layout:
#   "classic"   — every line keeps its own row and never moves; when a line is finished,
#                 only its row changes (to the line after next). Current + next 2 always visible.
#   "scrolling" — current line on top, the next two below; everything shifts up each line.
LYRIC_STYLE = "classic"
CLASSIC_ROWS_Y = [640, 765, 890]   # vertical centre of the three fixed rows
CLASSIC_SIZE = 74                  # font size of every row (shrinks only for very long lines)
BAND = True                        # soft dark band behind the lyrics for readability

TS = r"(\d+):(\d+(?:\.\d+)?)"


def t2s(m, s):
    return int(m) * 60 + float(s)


def ass_time(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_color(hexrgb, alpha="00"):
    h = hexrgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha}{b}{g}{r}&".upper()


def blend(hex_a, hex_b, k):
    """Mix colour a towards b by factor k (0..1)."""
    a = [int(hex_a.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(hex_b.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * k):02X}" for x, y in zip(a, b))


def esc(text):
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


# ----------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------
def parse_lyrics(path):
    meta, raw = {}, []
    singer = "M"
    with open(path, encoding="utf-8-sig") as f:
        for ln in f:
            ln = ln.rstrip("\n").strip()
            if not ln or ln.startswith("#"):
                continue
            m = re.match(r"^\[" + TS + r"\]\s*(.*)$", ln)
            if not m:
                mm = re.match(r"^\[?(title|artist|ti|ar|by)\s*:\s*(.*?)\]?$", ln, re.I)
                if mm:
                    key = {"ti": "title", "ar": "artist"}.get(mm.group(1).lower(), mm.group(1).lower())
                    meta[key] = mm.group(2).strip()
                continue
            start = t2s(m.group(1), m.group(2))
            text = m.group(3).strip()
            sm = re.match(r"^([MFD])\s*:\s*(.*)$", text, re.I)
            if sm:
                singer = sm.group(1).upper()
                text = sm.group(2)
            raw.append({"start": start, "text": text, "singer": singer})

    raw.sort(key=lambda r: r["start"])
    lines = []
    for i, r in enumerate(raw):
        if not r["text"]:
            continue  # blank timestamp: only marks an end time
        nxt = raw[i + 1]["start"] if i + 1 < len(raw) else None
        explicit_end = i + 1 < len(raw) and not raw[i + 1]["text"]
        words, cur_singer, pending_ts = [], r["singer"], None
        for tok in re.finditer(r"<" + TS + r">|\{([MFD])\}|([^\s<{]+)", r["text"], re.I):
            if tok.group(1) is not None:
                pending_ts = t2s(tok.group(1), tok.group(2))
            elif tok.group(3):
                cur_singer = tok.group(3).upper()
            else:
                words.append({"w": tok.group(4), "singer": cur_singer, "t": pending_ts})
                pending_ts = None
        if not words:
            continue
        lines.append({"start": r["start"], "next": nxt, "explicit_end": explicit_end,
                      "words": words, "singer": r["singer"]})

    # Work out when singing of each line ends and time every word.
    for ln in lines:
        s, nxt = ln["start"], ln["next"]
        nchars = sum(len(w["w"]) for w in ln["words"])
        if nxt is None:
            end = s + max(2.5, 0.2 * nchars)
        elif ln["explicit_end"] or nxt - s <= 10:
            end = nxt
        else:  # long gap with no end marker: guess the sung length
            end = min(nxt, s + max(2.5, 0.2 * nchars))
        last_ts = ln["words"][-1]["t"]
        if last_ts is not None and last_ts >= end:
            end = last_ts + 0.6
        ln["end"] = end
        time_words(ln)
    return meta, lines


def time_words(ln):
    """Fill each word's start/end. Uses <mm:ss> word stamps where given,
    otherwise spreads time between known points by word length."""
    words, s, e = ln["words"], ln["start"], ln["end"]
    if words[0]["t"] is None:
        words[0]["t"] = s
    anchors = [i for i, w in enumerate(words) if w["t"] is not None] + [len(words)]
    for a, b in zip(anchors, anchors[1:]):
        t0 = words[a]["t"]
        t1 = words[b]["t"] if b < len(words) else e
        seg = words[a:b]
        weights = [len(w["w"]) + 1 for w in seg]
        tot = sum(weights)
        t = t0
        for w, wt in zip(seg, weights):
            w["t"] = t
            t += (t1 - t0) * wt / tot
            w["e"] = t


# ----------------------------------------------------------------------------
# ASS subtitle generation
# ----------------------------------------------------------------------------
def fit_size(words, base):
    # Count visible letters only: Indian-script vowel signs/viramas combine
    # with the previous letter and take little or no extra width.
    def vis(t):
        return sum(1 for ch in t if unicodedata.category(ch) not in ("Mn", "Me", "Cf"))
    est = sum(vis(w["w"]) + 1 for w in words) * base * 0.60
    return base if est <= MAX_TEXT_W else max(28, int(base * MAX_TEXT_W / est))


def current_line_text(ln, state_start, y=None, base_size=None, marker=False, fade=None):
    """Karaoke line: words wipe from white to the singer colour."""
    size = fit_size(ln["words"], base_size or CUR_SIZE)
    y = SLOT_Y[0] if y is None else y
    head = f"\\an5\\pos({W // 2},{y})\\fs{size}\\2c{ass_color(UNSUNG)}"
    if fade:
        head += f"\\fad({fade[0]},{fade[1]})"
    parts = ["{" + head + "}"]
    if marker:   # coloured dot = who sings this line (shown before it is sung)
        c = SINGER_COLORS[ln["words"][0]["singer"]]
        parts.append(f"{{\\1c{ass_color(c)}\\fscx70\\fscy70}}● {{\\fscx100\\fscy100}}")
    lead = round((ln["words"][0]["t"] - state_start) * 100)
    if lead > 0:
        parts.append(f"{{\\k{lead}}}")
    cursor = ln["words"][0]["t"]
    for i, w in enumerate(ln["words"]):
        gap = round((w["t"] - cursor) * 100)
        if gap > 0:
            parts.append(f"{{\\k{gap}}}")
        dur = max(1, round((w["e"] - w["t"]) * 100))
        c = SINGER_COLORS[w["singer"]]
        parts.append(f"{{\\1c{ass_color(c)}\\kf{dur}}}{esc(w['w'])}")
        if i < len(ln["words"]) - 1:
            parts.append(" ")
        cursor = w["t"] + dur / 100
    return "".join(parts)


def preview_line_text(ln, slot):
    """Upcoming line: pale singer colour so you can see who sings next."""
    size = fit_size(ln["words"], NEXT_SIZE)
    parts = [f"{{\\an5\\pos({W // 2},{SLOT_Y[slot]})\\fs{size}\\alpha&H{PREVIEW_ALPHA}&\\bord3}}"]
    prev = None
    for i, w in enumerate(ln["words"]):
        if w["singer"] != prev:
            parts.append(f"{{\\1c{ass_color(blend(SINGER_COLORS[w['singer']], '#FFFFFF', 0.35))}}}")
            prev = w["singer"]
        parts.append(esc(w["w"]) + (" " if i < len(ln["words"]) - 1 else ""))
    return "".join(parts)


def build_ass(meta, lines, duration):
    out = []
    out.append("[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
               f"PlayResX: {W}\nPlayResY: {H}\n")
    out.append("[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
               "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
               "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
    out.append(f"Style: Lyric,{FONT},{CUR_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,"
               "-1,0,0,0,100,100,0,0,1,4,2,5,40,40,40,1")  # spacing must stay 0 for Indian scripts
    out.append(f"Style: Info,{FONT},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,"
               "-1,0,0,0,100,100,0,0,1,3,1,5,40,40,40,1\n")
    out.append("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")

    def ev(t0, t1, style, text, layer=0):
        if t1 > t0:
            out.append(f"Dialogue: {layer},{ass_time(t0)},{ass_time(t1)},{style},,0,0,0,,{text}")

    first = lines[0]["start"]
    intro_start = max(0.0, first - 3.0)

    # Title card
    title, artist = meta.get("title", ""), meta.get("artist", "")
    title_end = min(intro_start - 0.2, 8.0) if title else 0.0
    if title_end >= 1.5:
        ev(0, title_end, "Info",
           f"{{\\an5\\pos({W // 2},{H // 2 - 60})\\fs96\\fad(600,600)}}{esc(title)}"
           + (f"\\N{{\\fs52\\alpha&H40&}}{esc(artist)}" if artist else ""))

    # Countdown dots in the last 3 seconds before the first line
    if first >= 3.0 and LYRIC_STYLE != "classic":
        for k in range(3):
            dots = " ".join(["●"] * (3 - k))
            ev(first - 3 + k, first - 2 + k, "Info",
               f"{{\\an5\\pos({W // 2},{SLOT_Y[0] - 110})\\fs44\\1c{ass_color(SINGER_COLORS[lines[0]['words'][0]['singer']])}}}{dots}")

    # Singer colour legend (top-right), only for singers used in the song
    used = [s for s in "MFD" if any(w["singer"] == s for ln in lines for w in ln["words"])]
    legend = "   ".join(f"{{\\1c{ass_color(SINGER_COLORS[s])}}}● {{\\1c&HFFFFFF&}}{SINGER_NAMES[s]}" for s in used)
    ev(0, duration, "Info", f"{{\\an9\\pos({W - 50},40)\\fs34\\bord2}}{legend}", layer=1)

    if LYRIC_STYLE == "classic":
        _classic_events(lines, duration, intro_start, title_end, ev)
        return "\n".join(out) + "\n"

    # Main lyric states: line i is "current", i+1 and i+2 are previews.
    for i, ln in enumerate(lines):
        st = intro_start if i == 0 else lines[i]["start"]
        en = lines[i + 1]["start"] if i + 1 < len(lines) else min(duration, ln["end"] + 3)
        ev(st, en, "Lyric", current_line_text(ln, st), layer=2)
        for slot in (1, 2):
            if i + slot < len(lines):
                ev(st, en, "Lyric", preview_line_text(lines[i + slot], slot), layer=2)

    # Before intro: show the first lines as previews so singers can get ready
    t0 = title_end if title_end >= 1.5 else 0.0
    if intro_start - t0 > 0.5:
        for slot in range(min(3, len(lines))):
            ev(t0, intro_start, "Lyric", preview_line_text(lines[slot], slot), layer=2)
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------
# Video rendering
# ----------------------------------------------------------------------------
def _classic_events(lines, duration, intro_start, title_end, ev):
    """Fixed-row karaoke layout: line i lives in row i % 3 and never moves.
    It appears when line i-2 starts (so the current line and the next two are always
    visible) and leaves shortly after it has been sung, freeing its row for line i+3."""
    n = len(lines)
    rows = CLASSIC_ROWS_Y
    starts = [ln["start"] for ln in lines]
    ends = [ln["end"] for ln in lines]
    show_from = max(0.0, title_end + 0.3) if title_end >= 1.5 else 0.0

    appear, clear = [], []
    for i in range(n):
        a = show_from if i < 3 else starts[i - 2]
        c = min(starts[i + 1], ends[i] + 2.0) if i + 1 < n else min(duration, ends[i] + 3.0)
        c = max(c, ends[i])
        appear.append(a)
        clear.append(c)
    for i in range(3, n):       # never overlap the previous occupant of the same row
        appear[i] = max(appear[i], clear[i - 3])

    # soft dark band behind the lyric rows
    if BAND:
        top, bot = rows[0] - 75, rows[-1] + 75
        ev(show_from, min(duration, clear[-1] + 0.5), "Info",
           f"{{\\an7\\pos(0,{top})\\p1\\bord0\\shad0\\blur18\\1c&H000000&\\alpha&H78&\\fad(500,500)}}"
           f"m 0 0 l {W} 0 {W} {bot - top} 0 {bot - top}{{\\p0}}", layer=0)

    for i, ln in enumerate(lines):
        if clear[i] <= appear[i]:
            continue
        ev(appear[i], clear[i], "Lyric",
           current_line_text(ln, appear[i], y=rows[i % 3], base_size=CLASSIC_SIZE, marker=True,
                             fade=(200, 250)), layer=2)

    # Countdown before the first line and after long instrumental breaks
    for i in range(n):
        gap_start = intro_start if i == 0 else ends[i - 1]
        if starts[i] - gap_start < 4.0 or starts[i] < 3.0:
            continue
        col = ass_color(SINGER_COLORS[lines[i]["words"][0]["singer"]])
        y = rows[i % 3] - 62
        for k in range(3):
            ev(starts[i] - 3 + k, starts[i] - 2 + k, "Info",
               f"{{\\an5\\pos({W // 2},{y})\\fs30\\bord2\\1c{col}}}{' '.join(['●'] * (3 - k))}", layer=3)


def probe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", path], capture_output=True, text=True)
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return None


def shift_ass(ass_text, offset):
    """Move every subtitle earlier by `offset` seconds (for previews that start mid-song)."""
    def to_s(t):
        h, m, rest = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)
    out = []
    for ln in ass_text.splitlines():
        if ln.startswith("Dialogue:"):
            head, st, en, tail = ln.split(",", 3)
            a, b = to_s(st) - offset, to_s(en) - offset
            if b <= 0:
                continue
            ln = f"{head},{ass_time(a)},{ass_time(b)},{tail}"
        out.append(ln)
    return "\n".join(out) + "\n"


VOCAL_CUT = "pan=stereo|c0=c0-0.9*c1|c1=c1-0.9*c0,volume=1.6"   # rough centre-vocal removal


def render(ass_text, audio, bg, out, duration, preview_secs=None, start=0.0,
           progress=None, reduce_vocals=False, log=None, blur_bg=False):
    """Render the video. progress(fraction 0..1) is called while encoding."""
    tmp = tempfile.mkdtemp(prefix="karaoke_")
    try:
        if start > 0:
            ass_text = shift_ass(ass_text, start)
        with open(os.path.join(tmp, "k.ass"), "w", encoding="utf-8") as f:
            f.write(ass_text)
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1"]
        img_exts = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
        flags = 0x08000000 if sys.platform.startswith("win") else 0   # no console window on Windows
        if bg and bg.lower().endswith(img_exts):
            src = os.path.abspath(bg)
            if blur_bg:   # blur once up front so text on the cover doesn't fight the lyrics
                blurred = os.path.join(tmp, "bg.png")
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf",
                                f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                                "boxblur=18:2", "-frames:v", "1", blurred],
                               check=True, creationflags=flags)
                src = blurred
            cmd += ["-loop", "1", "-framerate", str(FPS), "-i", src]
        elif bg:
            cmd += ["-stream_loop", "-1", "-i", os.path.abspath(bg)]
        else:
            c = DEFAULT_BG.lstrip("#")
            cmd += ["-f", "lavfi", "-i",
                    f"gradients=s={W}x{H}:r={FPS}:c0=0x{c}:c1=0x2A0F3A:x0=0:y0=0:x1={W}:y1={H}:speed=0.004"]
        if audio:
            if start > 0:
                cmd += ["-ss", f"{start:.2f}"]
            cmd += ["-i", os.path.abspath(audio)]
        else:
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS}"
        if bg:
            vf += ",drawbox=color=black@0.45:t=fill"   # darken so lyrics stay readable
        vf += ",ass=k.ass:shaping=complex"   # complex shaping = correct Indian-script letters
        fc = f"[0:v]{vf}[v]"
        amap = "1:a:0"
        if reduce_vocals and audio:
            fc += f";[1:a:0]{VOCAL_CUT}[a]"
            amap = "[a]"
        t = preview_secs or max(0.5, duration - start)
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", amap,
                "-t", f"{t:.2f}",
                "-c:v", "libx264", "-preset", "veryfast" if preview_secs else "medium",
                "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", os.path.abspath(out)]
        proc = subprocess.Popen(cmd, cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", creationflags=flags)
        for line in proc.stdout:
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    secs = int(line.split("=")[1]) / 1e6
                except ValueError:
                    continue
                if progress:
                    progress(min(1.0, secs / t))
                else:
                    print(f"\r  {secs:6.1f}s / {t:.1f}s", end="", flush=True)
        err = proc.stderr.read()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg failed:\n" + err.strip()[-1500:])
        if progress:
            progress(1.0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    global FONT
    ap = argparse.ArgumentParser(description="Make a karaoke video with synced, colour-coded lyrics.")
    ap.add_argument("lyrics", help="timed lyrics file (.lrc / .txt)")
    ap.add_argument("--audio", help="song audio (mp3/wav/m4a...) — use the instrumental for karaoke")
    ap.add_argument("--bg", help="background image or video (optional)")
    ap.add_argument("--font", help=f'font name (default "{FONT}"), e.g. "Latha", "Mangal", "Noto Sans Tamil"')
    ap.add_argument("-o", "--out", help="output file (default: <lyrics name>_karaoke.mp4)")
    ap.add_argument("--ass-only", action="store_true", help="only write the .ass subtitle file")
    ap.add_argument("--preview", type=float, help="render only N seconds (quick check)")
    ap.add_argument("--start", type=float, default=0.0, help="with --preview: start at this many seconds")
    ap.add_argument("--reduce-vocals", action="store_true", help="rough vocal removal (centre channel)")
    ap.add_argument("--blur-bg", action="store_true", help="blur a background image")
    a = ap.parse_args()
    if a.font:
        FONT = a.font

    if not shutil.which("ffmpeg") and not a.ass_only:
        sys.exit("ffmpeg not found — install it and make sure it's on your PATH.")
    base = os.path.splitext(a.lyrics)[0]
    if not a.audio:  # look for song audio with the same name as the lyrics file
        for ext in (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"):
            if os.path.exists(base + ext):
                a.audio = base + ext
                print(f"Using audio: {a.audio}")
                break
        else:
            print("No audio found — making a silent video. Put mysong.mp3 next to mysong.lrc or use --audio.")
    if not a.out:
        a.out = base + ("_preview.mp4" if a.preview else "_karaoke.mp4")
    meta, lines = parse_lyrics(a.lyrics)
    if not lines:
        sys.exit("No timed lyric lines found. Lines must look like: [01:23.45] M: some words")

    duration = (probe_duration(a.audio) if a.audio else None) or (lines[-1]["end"] + 4)
    ass = build_ass(meta, lines, duration)

    if a.ass_only:
        out = a.out if a.out.lower().endswith(".ass") else os.path.splitext(a.out)[0] + ".ass"
        with open(out, "w", encoding="utf-8") as f:
            f.write(ass)
        print(f"Wrote {out}")
        return
    render(ass, a.audio, a.bg, a.out, duration, a.preview, a.start if a.preview else 0.0,
           reduce_vocals=a.reduce_vocals, blur_bg=a.blur_bg)
    print(f"\nDone → {a.out}")


if __name__ == "__main__":
    main()
