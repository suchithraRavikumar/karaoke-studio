#!/usr/bin/env python3
"""
Karaoke Studio — a desktop app for making karaoke videos with synced,
colour-coded (male / female / both) lyrics. Uses karaoke.py as its engine.

Workflow
  1. Pick the song (audio or video file) and, optionally, a background.
  2. Paste the lyrics (one line per lyric line).
  3. Press Play and tap SPACE as each line starts. M / F / D set the singer.
  4. Preview a short clip, then click "Make karaoke video".

Needs: Python 3.8+ with Tkinter, ffmpeg on PATH, and pygame (for playback).
"""
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser

HERE = os.path.dirname(os.path.abspath(__file__))
APP_VERSION = "1.4"
DEBUG_LOG = os.path.join(HERE, "karaoke_studio_debug.log")


def dlog(msg):
    """Small diagnostic log (helps find problems on a specific PC)."""
    try:
        import datetime
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%H:%M:%S} {msg}\n")
    except Exception:
        pass
sys.path.insert(0, HERE)

# Anaconda keeps ffmpeg in <env>\Library\bin; make sure we can find it.
for extra in (os.path.join(sys.prefix, "Library", "bin"), os.path.join(sys.prefix, "bin")):
    if os.path.isdir(extra) and extra not in os.environ.get("PATH", ""):
        os.environ["PATH"] = extra + os.pathsep + os.environ.get("PATH", "")

# winget installs ffmpeg under LocalAppData; add it if PATH hasn't caught up yet.
if not shutil.which("ffmpeg") and sys.platform.startswith("win"):
    import glob
    for pat in (r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin",
                r"%LOCALAPPDATA%\Microsoft\WinGet\Links", r"C:\ffmpeg\bin"):
        for d in glob.glob(os.path.expandvars(pat)):
            if os.path.exists(os.path.join(d, "ffmpeg.exe")):
                os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
                break

# Tools installed by winget (ffmpeg, Deno for YouTube) live here.
if sys.platform.startswith("win"):
    import glob
    for pat in (r"%LOCALAPPDATA%\Microsoft\WinGet\Links",
                r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\DenoLand.Deno*",
                r"%USERPROFILE%\.deno\bin"):
        for d in glob.glob(os.path.expandvars(pat)):
            if os.path.isdir(d) and d not in os.environ["PATH"]:
                os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]

import karaoke  # noqa: E402

try:
    import yt_dlp  # noqa: E402
    HAVE_YTDLP = True
except Exception:
    HAVE_YTDLP = False

SONGS_DIR = os.path.join(HERE, "songs")
VIDEOS_DIR = os.path.join(HERE, "videos")
YT_RE = re.compile(r"^(https?://)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/", re.I)

try:
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame  # noqa: E402
    HAVE_PYGAME = True
except Exception:
    HAVE_PYGAME = False

IS_WIN = sys.platform.startswith("win")
NO_WINDOW = 0x08000000 if IS_WIN else 0

AUDIO_TYPES = [("Song audio or video", "*.mp3 *.wav *.m4a *.flac *.ogg *.aac *.mp4 *.mkv *.webm *.mov *.avi"),
               ("All files", "*.*")]
BG_TYPES = [("Image or video", "*.jpg *.jpeg *.png *.webp *.bmp *.mp4 *.mkv *.webm *.mov *.avi"),
            ("All files", "*.*")]
FONTS = ["Nirmala UI", "Latha", "Mangal", "Gautami", "Tunga", "Kartika", "Vrinda",
         "Shruti", "Raavi", "Kalinga", "Arial", "Segoe UI", "Noto Sans", "Noto Sans Tamil"]

# UI palette
BG, PANEL, CARD, LINE = "#15151F", "#1E1E2B", "#262636", "#34344A"
TEXT, MUTED, ACCENT = "#ECECF4", "#9A9AB2", "#7C6CFF"

TS_RE = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)$")
TOKEN_RE = re.compile(r"<(\d+):(\d+(?:\.\d+)?)>|\{([MFDmfd])\}|([^\s<{]+)")
LRC_TAG_RE = re.compile(r"^\[(ti|ar|al|by|length|offset|re|ve|au|la|id)\s*:\s*(.*?)\]$", re.I)
META_RE = re.compile(r"^(title|artist)\s*:\s*(.+)$", re.I)
SRT_TIME_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
LANGS = [("Auto (from lyrics)", None), ("Tamil", "ta"), ("Hindi", "hi"), ("Telugu", "te"),
         ("Kannada", "kn"), ("Malayalam", "ml"), ("Bengali", "bn"), ("Marathi", "mr"),
         ("Gujarati", "gu"), ("Punjabi", "pa"), ("Urdu", "ur"), ("English", "en")]
SCRIPT_LANG = [((0x0B80, 0x0BFF), "ta"), ((0x0900, 0x097F), "hi"), ((0x0C00, 0x0C7F), "te"),
               ((0x0C80, 0x0CFF), "kn"), ((0x0D00, 0x0D7F), "ml"), ((0x0980, 0x09FF), "bn"),
               ((0x0A80, 0x0AFF), "gu"), ((0x0A00, 0x0A7F), "pa"), ((0x0600, 0x06FF), "ur")]


def word_tokens(text):
    """Words in a lyric line, the same way karaoke.py splits them (singer tags excluded)."""
    return [m.group(4) for m in TOKEN_RE.finditer(text) if m.group(4)]


def split_stamps(text):
    """'<00:12.00>Hello <00:12.50>world' -> ('Hello world', [12.0, 12.5]). Times may be None."""
    out, times, pending = [], [], None
    for m in TOKEN_RE.finditer(text):
        if m.group(1) is not None:
            pending = int(m.group(1)) * 60 + float(m.group(2))
        elif m.group(3):
            out.append("{" + m.group(3).upper() + "}")
        else:
            out.append(m.group(4))
            times.append(pending)
            pending = None
    return " ".join(out), (times if any(t is not None for t in times) else None)


def with_stamps(text, words):
    """Inverse of split_stamps: put <mm:ss.xx> before each word that has a time."""
    if not words:
        return text
    out, k = [], 0
    for m in TOKEN_RE.finditer(text):
        if m.group(3):
            out.append("{" + m.group(3).upper() + "}")
        elif m.group(4):
            t = words[k] if k < len(words) else None
            out.append((f"<{fmt(t)}>" if t is not None else "") + m.group(4))
            k += 1
    return " ".join(out)


def guess_lang(text):
    counts = {}
    for ch in text:
        o = ord(ch)
        for (a, b), code in SCRIPT_LANG:
            if a <= o <= b:
                counts[code] = counts.get(code, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    return "en"


def parse_lyric_text(raw, default_singer="M", breaks=True):
    """Read pasted text, a plain .txt, a synced .lrc (incl. word-level <mm:ss> stamps) or an .srt.
    Returns (rows, meta, timed)."""
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    meta, rows = {}, []
    # SRT subtitles
    if SRT_TIME_RE.search(raw):
        for block in re.split(r"\n\s*\n", raw.strip()):
            ls = [x.strip() for x in block.split("\n") if x.strip()]
            for i, x in enumerate(ls):
                m = SRT_TIME_RE.search(x)
                if m:
                    g = [int(v) for v in m.groups()]
                    st = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                    text = " ".join(re.sub(r"<[^>]+>", "", y) for y in ls[i + 1:]).strip()
                    if text:
                        rows.append({"t": st, "singer": default_singer, "text": text, "words": None})
                    break
        return rows, meta, True

    singer = default_singer
    offset = 0.0
    timed = False
    for ln in raw.split("\n"):
        s_ = ln.strip()
        if not s_:
            if breaks and not timed and rows and rows[-1]["text"]:
                rows.append({"t": None, "singer": singer, "text": "", "words": None})
            continue
        m = LRC_TAG_RE.match(s_)
        if m:
            k, v = m.group(1).lower(), m.group(2).strip()
            if k == "ti":
                meta["title"] = v
            elif k == "ar":
                meta["artist"] = v
            elif k == "offset":
                try:
                    offset = float(v) / 1000.0
                except ValueError:
                    pass
            continue
        m = META_RE.match(s_)
        if m and not rows:
            meta[m.group(1).lower()] = m.group(2).strip()
            continue
        if s_.startswith("#"):
            continue
        stamps = []
        while True:
            m = re.match(r"^\[(\d+):(\d+(?:\.\d+)?)\]\s*", s_)
            if not m:
                break
            stamps.append(int(m.group(1)) * 60 + float(m.group(2)))
            s_ = s_[m.end():]
        sm = re.match(r"^([MFDmfd])\s*[:：]\s*(.*)$", s_)
        who = singer if stamps else default_singer
        if sm:
            who, s_ = sm.group(1).upper(), sm.group(2).strip()
            singer = who
        text, words = split_stamps(s_)
        if stamps:
            timed = True
            for st in stamps:
                ws = None
                if words:
                    shift = st - (words[0] if words[0] is not None else st)
                    ws = [None if w is None else max(0.0, w + shift - offset) for w in words] \
                        if len(stamps) > 1 else [None if w is None else max(0.0, w - offset) for w in words]
                rows.append({"t": max(0.0, st - offset), "singer": who, "text": text, "words": ws})
        else:
            rows.append({"t": None, "singer": who, "text": text, "words": words})
    while rows and not rows[-1]["text"] and rows[-1]["t"] is None:
        rows.pop()
    if timed:
        rows = [r for r in rows if r["t"] is not None]
        rows.sort(key=lambda r: r["t"])
    return rows, meta, timed


def map_alignment(line_texts, aligned_words):
    """Map aligned words ([text, start, end], in order) back onto our lyric lines.
    Works on character positions (spaces ignored), so it copes with the model splitting
    or merging words differently. Returns per line: (start, end, [word times])."""
    def clean(t):
        return re.sub(r"\s+", "", t)

    import difflib
    res_pos, pos, rchars = [], 0, []
    for w, st, en in aligned_words:
        c = clean(w)
        res_pos.append((pos, len(c), st, en))
        rchars.append(c)
        pos += len(c)
    R = "".join(rchars)
    L = "".join(clean(tok) for t in line_texts for tok in word_tokens(t))
    # character map lyric -> model output (they're normally identical; this copes with small differences)
    cmap = [None] * (len(L) + 1)
    for a, b, n in difflib.SequenceMatcher(None, L, R, autojunk=False).get_matching_blocks():
        for k in range(n):
            cmap[a + k] = b + k
    cmap[len(L)] = len(R)
    known = [(i, v) for i, v in enumerate(cmap) if v is not None]
    if not known or known[0][0] != 0:
        known.insert(0, (0, 0))
    j = 0
    for i in range(len(cmap)):
        if cmap[i] is None:
            while j + 1 < len(known) and known[j + 1][0] < i:
                j += 1
            (i0, v0) = known[j]
            (i1, v1) = known[j + 1] if j + 1 < len(known) else (len(L), len(R))
            cmap[i] = v0 + (v1 - v0) * (i - i0) / max(1, i1 - i0)

    def time_at(char_pos, use_end=False):
        cp = cmap[min(len(cmap) - 1, max(0, char_pos))]
        best = res_pos[0]
        for item in res_pos:
            if item[0] <= cp:
                best = item
            else:
                break
        p0, ln, st, en = best
        if use_end:
            return en
        if ln <= 0 or en <= st:
            return st
        frac = min(1.0, max(0.0, (cp - p0) / ln))
        return st + frac * (en - st)

    out, cpos, last = [], 0, 0.0
    for t in line_texts:
        toks = word_tokens(t)
        times = []
        for tok in toks:
            tt = max(time_at(cpos), last + 0.01 if times or out else 0.0)
            times.append(round(tt, 2))
            last = tt
            cpos += len(clean(tok))
        end = max(time_at(max(0, cpos - 1), use_end=True), last + 0.2)
        out.append((times[0] if times else round(last, 2), round(end, 2), times))
    return out


def fmt(t):
    if t is None:
        return "--:--.--"
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:05.2f}"


def parse_time(txt):
    txt = txt.strip()
    m = re.match(r"^(\d+):(\d+(?:\.\d+)?)$", txt)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    return float(txt)


def open_path(path):
    if IS_WIN:
        os.startfile(path)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# ---------------------------------------------------------------------------
# Audio player (pygame). Converts anything that isn't mp3/ogg to a temp .ogg
# with ffmpeg so seeking works and video files can be used as the song.
# ---------------------------------------------------------------------------
class Player:
    def __init__(self):
        self.ok = False
        self.path = None
        self.duration = 0.0
        self.playing = False
        self._offset = 0.0
        self._t0 = 0.0
        self._tmp = tempfile.mkdtemp(prefix="kstudio_")
        if HAVE_PYGAME:
            try:
                pygame.mixer.init(frequency=44100)
                self.ok = True
            except Exception:
                self.ok = False

    def load(self, src):
        self.stop()
        self.path = None
        self.duration = karaoke.probe_duration(src) or 0.0
        if not self.ok:
            return
        play_path = src
        if not src.lower().endswith((".mp3", ".ogg")):
            play_path = os.path.join(self._tmp, f"play_{int(time.time())}.ogg")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vn", "-ac", "2",
                            "-ar", "44100", "-c:a", "libvorbis", "-q:a", "5", play_path],
                           check=True, creationflags=NO_WINDOW)
        pygame.mixer.music.load(play_path)
        self.path = play_path

    def position(self):
        if self.playing:
            return self._offset + (time.perf_counter() - self._t0)
        return self._offset

    def play(self, start=None):
        if not (self.ok and self.path):
            return
        if start is not None:
            self._offset = max(0.0, min(start, max(0.0, self.duration - 0.05)))
        pygame.mixer.music.play(start=self._offset)
        self._t0 = time.perf_counter()
        self.playing = True

    def pause(self):
        if self.playing:
            self._offset = self.position()
            pygame.mixer.music.stop()
            self.playing = False

    def seek(self, t):
        was = self.playing
        self.pause()
        self._offset = max(0.0, min(t, self.duration or t))
        if was:
            self.play()

    def stop(self):
        if self.ok:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        self.playing = False

    def cleanup(self):
        self.stop()
        if self.ok:
            try:
                pygame.mixer.music.unload()
            except Exception:
                pass
        shutil.rmtree(self._tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
class Studio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Karaoke Studio  v{APP_VERSION}")
        self.geometry("1360x860")
        self.minsize(1000, 680)
        if IS_WIN:
            try:
                self.state("zoomed")   # start maximised — nothing gets cut off on scaled screens
            except tk.TclError:
                pass
        self.configure(bg=BG)
        try:   # window / taskbar icon
            ico = os.path.join(HERE, "karaoke_studio.ico")
            png = os.path.join(HERE, "karaoke_studio.png")
            if IS_WIN and os.path.exists(ico):
                self.iconbitmap(default=ico)
            elif os.path.exists(png):
                self._icon_img = tk.PhotoImage(file=png).subsample(8, 8)
                self.iconphoto(True, self._icon_img)
        except Exception:
            pass

        self.rows = []            # [{"t": float|None, "singer": "M"/"F"/"D", "text": str}]
        self.project_path = None
        self.dirty = False
        self.colors = dict(karaoke.SINGER_COLORS)
        self.player = Player()
        self.jobs = queue.Queue()
        self.busy = False
        self.last_output = None

        self.v_audio = tk.StringVar()
        self.v_bg = tk.StringVar()
        self.v_title = tk.StringVar()
        self.v_artist = tk.StringVar()
        self.v_font = tk.StringVar(value=karaoke.FONT)
        self.v_out = tk.StringVar()
        self.v_vocals = tk.BooleanVar(value=False)
        self.v_blur = tk.BooleanVar(value=True)
        self.v_url = tk.StringVar()
        self.yt_busy = False
        self.v_status = tk.StringVar(value="Start by choosing the song, then paste the lyrics.")
        self.v_time = tk.StringVar(value="00:00.00 / 00:00.00")
        self.v_default_singer = tk.StringVar(value="M")
        self.v_edit_text = tk.StringVar()
        self.v_edit_time = tk.StringVar()
        self.v_edit_singer = tk.StringVar(value="M")
        self._seek_drag = False

        self._style()
        self._build()
        self._keys()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(40, self._tick)
        self.after(1500, lambda: dlog(
            f"START v{APP_VERSION} py={sys.version.split()[0]} tk={self.tk.call('info', 'patchlevel')} "
            f"scaling={self.tk.call('tk', 'scaling')} screen={self.winfo_screenwidth()}x{self.winfo_screenheight()} "
            f"win={self.winfo_width()}x{self.winfo_height()} font={self.v_font.get()} file={__file__}"))
        self.after(200, self._startup_checks)

    # ---------------------------------------------------------------- style
    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        base_font = ("Segoe UI", 10) if IS_WIN else ("DejaVu Sans", 10)
        self.base_font = base_font
        st.configure(".", background=PANEL, foreground=TEXT, fieldbackground=CARD,
                     bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, font=base_font)
        st.configure("TFrame", background=PANEL)
        st.configure("Bg.TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD)
        st.configure("TLabel", background=PANEL, foreground=TEXT)
        st.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        st.configure("H.TLabel", background=PANEL, foreground=TEXT, font=(base_font[0], 11, "bold"))
        st.configure("Title.TLabel", background=BG, foreground=TEXT, font=(base_font[0], 16, "bold"))
        st.configure("Sub.TLabel", background=BG, foreground=MUTED)
        st.configure("Time.TLabel", background=PANEL, foreground=TEXT, font=("Consolas" if IS_WIN else "DejaVu Sans Mono", 13))
        st.configure("TButton", background=CARD, foreground=TEXT, padding=(10, 5), borderwidth=1)
        st.map("TButton", background=[("active", LINE), ("disabled", PANEL)],
               foreground=[("disabled", MUTED)])
        st.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF",
                     font=(base_font[0], 11, "bold"), padding=(14, 9))
        st.map("Accent.TButton", background=[("active", "#6A5AF0"), ("disabled", LINE)])
        st.configure("Tap.TButton", background="#2E2E48", foreground=TEXT,
                     font=(base_font[0], 12, "bold"), padding=(18, 10))
        st.map("Tap.TButton", background=[("active", ACCENT)])
        st.configure("Small.TButton", background=CARD, foreground=TEXT, padding=(7, 5))
        st.map("Small.TButton", background=[("active", LINE)])
        st.configure("Sync.TButton", background="#2B6E5A", foreground="#FFFFFF",
                     font=(base_font[0], 10, "bold"), padding=(10, 5))
        st.map("Sync.TButton", background=[("active", "#34876E"), ("disabled", LINE)])
        st.configure("TEntry", fieldbackground=CARD, foreground=TEXT, insertcolor=TEXT, padding=4)
        st.configure("TCombobox", fieldbackground=CARD, foreground=TEXT, background=CARD, arrowcolor=TEXT)
        st.map("TCombobox", fieldbackground=[("readonly", CARD)], foreground=[("readonly", TEXT)])
        st.configure("TCheckbutton", background=PANEL, foreground=TEXT)
        st.map("TCheckbutton", background=[("active", PANEL)])
        st.configure("TRadiobutton", background=PANEL, foreground=TEXT)
        st.map("TRadiobutton", background=[("active", PANEL)])
        st.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                     rowheight=30, borderwidth=0, font=(self.v_font.get(), 12))
        st.configure("Treeview.Heading", background=PANEL, foreground=MUTED,
                     font=(base_font[0], 9, "bold"), relief="flat")
        st.map("Treeview", background=[("selected", "#3B3470")], foreground=[("selected", "#FFFFFF")])
        st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=CARD, bordercolor=LINE)
        st.configure("Horizontal.TScale", background=PANEL, troughcolor=CARD)

    # ---------------------------------------------------------------- layout
    def _build(self):
        top = ttk.Frame(self, style="Bg.TFrame", padding=(16, 12, 16, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Karaoke Studio", style="Title.TLabel").pack(side="left")
        ttk.Label(top, text="   synced lyrics · male / female / both colours · next 2 lines",
                  style="Sub.TLabel").pack(side="left", pady=(5, 0))
        for txt, cmd in (("Save project", self.save_project), ("Open project…", self.open_project),
                         ("New", self.new_project)):
            ttk.Button(top, text=txt, command=cmd).pack(side="right", padx=(6, 0))

        body = ttk.Frame(self, style="Bg.TFrame", padding=(16, 4, 16, 12))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_left(body)
        self._build_center(body)

        bar = ttk.Frame(self, style="Bg.TFrame", padding=(16, 0, 16, 10))
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.v_status, style="Sub.TLabel").pack(side="left")

    def _section(self, parent, title, step=None):
        f = ttk.Frame(parent, padding=(14, 12))
        head = ttk.Frame(f)
        head.pack(fill="x", pady=(0, 8))
        if step:
            tk.Label(head, text=step, bg=ACCENT, fg="white", width=2,
                     font=(self.base_font[0], 9, "bold")).pack(side="left", padx=(0, 8))
        ttk.Label(head, text=title, style="H.TLabel").pack(side="left")
        return f

    def _file_row(self, parent, label, var, cmd, clear=False):
        ttk.Label(parent, text=label, style="Muted.TLabel").pack(anchor="w")
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(2, 8))
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=cmd).pack(side="left", padx=(6, 0))
        if clear:
            ttk.Button(row, text="✕", width=3, command=lambda: var.set("")).pack(side="left", padx=(4, 0))

    def _build_left(self, body):
        left = ttk.Frame(body, style="Bg.TFrame", width=360)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 12))

        s1 = self._section(left, "Song & background", "1")
        s1.pack(fill="x", pady=(0, 10))
        ttk.Label(s1, text="YouTube link → audio + cover image", style="Muted.TLabel").pack(anchor="w")
        yr = ttk.Frame(s1)
        yr.pack(fill="x", pady=(2, 4))
        self.url_entry = ttk.Entry(yr, textvariable=self.v_url)
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.bind("<Return>", lambda e: self.fetch_youtube())
        self.url_entry.bind("<Button-3>", lambda e: self._paste_url())
        self.btn_yt = ttk.Button(yr, text="Get", width=6, command=self.fetch_youtube)
        self.btn_yt.pack(side="left", padx=(6, 0))
        self.yt_bar = ttk.Progressbar(s1, mode="determinate", maximum=1000)
        self.yt_bar.pack(fill="x", pady=(0, 10))
        self._file_row(s1, "Song (audio, or a video with the song)", self.v_audio, self.pick_audio)
        self._file_row(s1, "Cover / background image or video (optional)", self.v_bg, self.pick_bg, clear=True)
        ttk.Checkbutton(s1, text="Blur the cover image (lyrics easier to read)",
                        variable=self.v_blur).pack(anchor="w")
        ttk.Checkbutton(s1, text="Reduce vocals in the video (quick, rough)",
                        variable=self.v_vocals).pack(anchor="w")
        ttk.Label(s1, text="For clean karaoke use an instrumental track.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        s2 = self._section(left, "Title & look", "3")
        s2.pack(fill="x", pady=(0, 10))
        g = ttk.Frame(s2)
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        for r, (lbl, var) in enumerate((("Title", self.v_title), ("Artist", self.v_artist))):
            ttk.Label(g, text=lbl, style="Muted.TLabel").grid(row=r, column=0, sticky="w", pady=3)
            ttk.Entry(g, textvariable=var).grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3)
        ttk.Label(g, text="Font", style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=3)
        cb = ttk.Combobox(g, textvariable=self.v_font, values=FONTS)
        cb.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=3)
        cb.bind("<<ComboboxSelected>>", lambda e: self._font_changed())
        cb.bind("<FocusOut>", lambda e: self._font_changed())

        cl = ttk.Frame(s2)
        cl.pack(fill="x", pady=(8, 0))
        self.color_btns = {}
        for s, name in (("M", "Male"), ("F", "Female"), ("D", "Both")):
            b = tk.Button(cl, text=name, relief="flat", bd=0, padx=10, pady=5, cursor="hand2",
                          font=(self.base_font[0], 10, "bold"), fg="#101018",
                          command=lambda s=s: self.pick_color(s))
            b.pack(side="left", padx=(0, 6), fill="x", expand=True)
            self.color_btns[s] = b
        self._paint_color_btns()
        ttk.Label(s2, text="Click a colour to change it.", style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        s3 = self._section(left, "Make the video", "4")
        s3.pack(fill="x")
        self._file_row(s3, "Save video as", self.v_out, self.pick_out)
        pr = ttk.Frame(s3)
        pr.pack(fill="x", pady=(0, 8))
        self.btn_preview = ttk.Button(pr, text="▶ Preview 20 s from selected line",
                                      command=self.preview_clip)
        self.btn_preview.pack(fill="x")
        self.btn_render = ttk.Button(s3, text="Make karaoke video", style="Accent.TButton",
                                     command=self.render_full)
        self.btn_render.pack(fill="x")
        self.pbar = ttk.Progressbar(s3, mode="determinate", maximum=1000)
        self.pbar.pack(fill="x", pady=(10, 4))
        done = ttk.Frame(s3)
        done.pack(fill="x")
        self.btn_open = ttk.Button(done, text="Open video", command=self.open_output, state="disabled")
        self.btn_open.pack(side="left", fill="x", expand=True)
        self.btn_folder = ttk.Button(done, text="Show in folder", command=self.open_folder, state="disabled")
        self.btn_folder.pack(side="left", fill="x", expand=True, padx=(6, 0))

    def _build_center(self, body):
        center = ttk.Frame(body, style="Bg.TFrame")
        center.grid(row=0, column=1, sticky="nsew")
        center.rowconfigure(1, weight=1)
        center.columnconfigure(0, weight=1)

        # --- live preview + player
        pv = ttk.Frame(center, padding=(14, 12))
        pv.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        pv.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(pv, height=170, bg="#12122A", highlightthickness=0)
        self.canvas.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.canvas.bind("<Configure>", lambda e: self._draw_preview(force=True))

        ctl = ttk.Frame(pv)
        ctl.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.btn_play = ttk.Button(ctl, text="▶  Play", width=10, command=self.toggle_play)
        self.btn_play.pack(side="left")
        ttk.Button(ctl, text="⟲ 5s", width=6, command=lambda: self.nudge_play(-5)).pack(side="left", padx=(6, 0))
        ttk.Button(ctl, text="5s ⟳", width=6, command=lambda: self.nudge_play(5)).pack(side="left", padx=(4, 0))
        ttk.Button(ctl, text="⏮ Play from line", command=self.play_from_selected).pack(side="left", padx=(6, 0))
        self.btn_tap = ttk.Button(ctl, text="TAP  (Space)", style="Tap.TButton", command=self.tap)
        self.btn_tap.pack(side="right")
        ttk.Label(ctl, textvariable=self.v_time, style="Time.TLabel").pack(side="right", padx=(0, 14))
        self.seek = ttk.Scale(pv, from_=0, to=1, orient="horizontal", command=self._on_seek_drag)
        self.seek.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.seek.bind("<ButtonPress-1>", lambda e: setattr(self, "_seek_drag", True))
        self.seek.bind("<ButtonRelease-1>", self._on_seek_release)

        # --- lyrics table
        lf = ttk.Frame(center, padding=(14, 12))
        lf.grid(row=1, column=0, sticky="nsew")
        lf.rowconfigure(2, weight=1)
        lf.columnconfigure(0, weight=1)

        head = ttk.Frame(lf)
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Label(head, text="2", bg=ACCENT, fg="white", width=2,
                 font=(self.base_font[0], 9, "bold")).pack(side="left", padx=(0, 8))
        ttk.Label(head, text="Lyrics & timing", style="H.TLabel").pack(side="left")
        ttk.Label(head, text="   Play the song and press SPACE as each line starts  ·  M / F / D = singer",
                  style="Muted.TLabel").pack(side="left")

        tb = ttk.Frame(lf)
        tb.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 8))
        ttk.Button(tb, text="Paste lyrics…", style="Small.TButton",
                   command=lambda: (dlog("CLICK Paste lyrics"), self.paste_dialog())).pack(side="left")
        ttk.Button(tb, text="Open lyrics file…", style="Small.TButton",
                   command=self.import_lyrics).pack(side="left", padx=(4, 0))
        self.btn_sync = ttk.Button(tb, text="✨ Auto-sync", style="Sync.TButton", command=self.auto_sync)
        self.btn_sync.pack(side="left", padx=(8, 12))
        ttk.Button(tb, text="+ Line", style="Small.TButton", command=self.add_line).pack(side="left")
        ttk.Button(tb, text="+ Break", style="Small.TButton", command=self.add_break).pack(side="left", padx=(4, 0))
        ttk.Button(tb, text="Delete", style="Small.TButton", command=self.delete_rows).pack(side="left", padx=(4, 0))
        ttk.Button(tb, text="↑", width=2, style="Small.TButton",
                   command=lambda: self.move_row(-1)).pack(side="left", padx=(4, 0))
        ttk.Button(tb, text="↓", width=2, style="Small.TButton",
                   command=lambda: self.move_row(1)).pack(side="left", padx=(2, 0))
        ttk.Button(tb, text="Clear times", style="Small.TButton",
                   command=self.clear_times).pack(side="left", padx=(4, 0))

        cols = ("n", "time", "singer", "text")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", selectmode="extended")
        for c, txt, w, anchor, stretch in (("n", "#", 44, "e", False), ("time", "START", 96, "center", False),
                                           ("singer", "SINGER", 90, "center", False),
                                           ("text", "LYRICS", 600, "w", True)):
            self.tree.heading(c, text=txt, anchor=anchor if c != "text" else "w")
            self.tree.column(c, width=w, anchor=anchor, stretch=stretch)
        self.tree.grid(row=2, column=0, sticky="nsew")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        sb.grid(row=2, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._load_editor())
        self.tree.bind("<Double-1>", lambda e: self.edit_entry.focus_set())
        self._tag_colors()

        ed = ttk.Frame(lf)
        ed.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(ed, text="Edit line", style="Muted.TLabel").pack(side="left")
        for s, name in (("M", "Male"), ("F", "Female"), ("D", "Both")):
            ttk.Radiobutton(ed, text=name, value=s, variable=self.v_edit_singer,
                            command=self.apply_edit).pack(side="left", padx=(8, 0))
        ttk.Label(ed, text="Start", style="Muted.TLabel").pack(side="left", padx=(12, 4))
        te = ttk.Entry(ed, textvariable=self.v_edit_time, width=9)
        te.pack(side="left")
        te.bind("<Return>", lambda e: self.apply_edit())
        ttk.Button(ed, text="−0.1", width=5, command=lambda: self.nudge_time(-0.1)).pack(side="left", padx=(4, 0))
        ttk.Button(ed, text="+0.1", width=5, command=lambda: self.nudge_time(0.1)).pack(side="left", padx=(2, 0))
        self.edit_entry = ttk.Entry(ed, textvariable=self.v_edit_text, font=(self.v_font.get(), 12))
        self.edit_entry.pack(side="left", fill="x", expand=True, padx=(10, 6))
        self.edit_entry.bind("<Return>", lambda e: self.apply_edit())
        ttk.Button(ed, text="Apply", command=self.apply_edit).pack(side="left")
        ttk.Label(lf, text="Tip: type {F}, {M} or {D} inside a line to switch singer mid-line.",
                  style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(6, 0))

    def _tag_colors(self):
        for s, c in self.colors.items():
            self.tree.tag_configure(s, foreground=karaoke.blend(c, "#FFFFFF", 0.15))
        self.tree.tag_configure("B", foreground=MUTED)
        self.tree.tag_configure("now", background="#2F2A55")

    def _paint_color_btns(self):
        for s, b in self.color_btns.items():
            b.configure(bg=self.colors[s], activebackground=karaoke.blend(self.colors[s], "#FFFFFF", 0.3))

    # ---------------------------------------------------------------- keys
    def _keys(self):
        def typing():
            w = self.focus_get()
            return isinstance(w, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Text))

        def k(func):
            return lambda e: None if typing() else (func(), "break")[1]

        self.bind_all("<space>", k(self.tap))
        for s in "MFD":
            self.bind_all(f"<{s.lower()}>", k(lambda s=s: self.set_singer(s)))
            self.bind_all(f"<{s}>", k(lambda s=s: self.set_singer(s)))
        self.bind_all("<p>", k(self.toggle_play))
        self.bind_all("<Left>", k(lambda: self.nudge_play(-2)))
        self.bind_all("<Right>", k(lambda: self.nudge_play(2)))
        self.bind_all("<Delete>", k(self.delete_rows))
        self.bind_all("<Control-s>", lambda e: self.save_project())
        self.bind_all("<Escape>", lambda e: self.tree.focus_set())

    # ---------------------------------------------------------------- startup
    def _startup_checks(self):
        problems = []
        if not shutil.which("ffmpeg"):
            problems.append("• ffmpeg was not found. Run Setup.bat (or: conda install -c conda-forge ffmpeg).")
        if not self.player.ok:
            problems.append("• pygame is not installed, so the song can't play inside the app "
                            "(tap-to-sync needs it). Run Setup.bat (or: pip install pygame).")
        if not HAVE_YTDLP:
            problems.append("• The YouTube downloader (yt-dlp) is not installed, so YouTube links won't work. "
                            "Run Setup.bat again.")
        if problems:
            messagebox.showwarning("Almost ready", "\n\n".join(problems))

    # ---------------------------------------------------------------- files
    def pick_audio(self):
        p = filedialog.askopenfilename(title="Choose the song", filetypes=AUDIO_TYPES)
        if p:
            self.set_audio(p)

    # ---------------------------------------------------------------- YouTube
    def _paste_url(self):
        try:
            self.v_url.set(self.clipboard_get().strip())
        except tk.TclError:
            pass

    def fetch_youtube(self):
        if self.yt_busy:
            return
        url = self.v_url.get().strip()
        if not url:
            self._paste_url()
            url = self.v_url.get().strip()
        if not YT_RE.match(url):
            messagebox.showerror("YouTube link", "Paste a YouTube link, e.g.\nhttps://www.youtube.com/watch?v=…")
            return
        if not HAVE_YTDLP:
            messagebox.showerror("YouTube", "The YouTube downloader isn't installed yet.\nRun Setup.bat again.")
            return
        if not shutil.which("ffmpeg"):
            messagebox.showerror("ffmpeg missing", "ffmpeg isn't installed. Run Setup.bat first.")
            return
        if any(r["text"] for r in self.rows):
            ans = messagebox.askyesnocancel("New song", "Start a new song?\n\nYes = clear the current lyrics\n"
                                                        "No = keep them (just replace the audio and cover)")
            if ans is None:
                return
            if ans:
                if self.dirty and self.project_path:
                    self.save_project()
                self.rows = []
                self.project_path = None
                self.refresh([])
        self.yt_busy = True
        self.btn_yt.state(["disabled"])
        self.yt_bar["value"] = 0
        self.status("Getting audio and cover from YouTube…")
        os.makedirs(SONGS_DIR, exist_ok=True)
        threading.Thread(target=self._yt_work, args=(url,), daemon=True).start()

    def _yt_work(self, url):
        def hook(d):
            if d.get("status") == "downloading":
                tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if tot:
                    self.jobs.put(("yt_progress", d.get("downloaded_bytes", 0) / tot))
            elif d.get("status") == "finished":
                self.jobs.put(("yt_progress", 1.0))
                self.jobs.put(("yt_status", "Converting to mp3…"))

        ff = shutil.which("ffmpeg")
        opts = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "outtmpl": {"default": os.path.join(SONGS_DIR, "%(title).70B [%(id)s].%(ext)s")},
            "windowsfilenames": True,
            "writethumbnail": True,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
            ],
            "progress_hooks": [hook],
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
        }
        if ff:
            opts["ffmpeg_location"] = os.path.dirname(ff)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info.get("_type") == "playlist" and info.get("entries"):
                    info = info["entries"][0]
                base = os.path.splitext(ydl.prepare_filename(info))[0]
            audio = None
            for rd in info.get("requested_downloads") or []:
                fp = rd.get("filepath")
                if fp and os.path.exists(fp):
                    audio = fp
            if not audio and os.path.exists(base + ".mp3"):
                audio = base + ".mp3"
            if not audio:
                raise RuntimeError("The download finished but the mp3 file wasn't found.")
            cover = None
            for ext in (".jpg", ".jpeg", ".png", ".webp"):
                if os.path.exists(base + ext):
                    cover = base + ext
                    break
            raw_title = info.get("track") or info.get("title") or ""
            title = re.split(r"\s*[|｜]\s*", raw_title)[0].strip()
            title = re.sub(r"\s*[\(\[](official|full|lyric|video|audio|hd|4k)[^\)\]]*[\)\]]", "",
                           title, flags=re.I).strip() or raw_title
            artist = info.get("artist") or info.get("creator") or ""
            self.jobs.put(("yt_done", audio, cover, title, artist))
        except Exception as e:
            msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
            self.jobs.put(("yt_error", msg))

    def _yt_finish(self, audio, cover, title, artist):
        self.yt_busy = False
        self.btn_yt.state(["!disabled"])
        self.yt_bar["value"] = 1000
        self.v_title.set(title)
        self.v_artist.set(artist)
        if cover:
            self.v_bg.set(cover)
        os.makedirs(VIDEOS_DIR, exist_ok=True)
        base = re.sub(r'[\\/:*?"<>|｜]+', " ", title).strip() or "song"
        self.v_out.set(os.path.join(VIDEOS_DIR, base + "_karaoke.mp4"))
        self.v_url.set("")
        self.set_audio(audio)
        self.status(f"Got “{title}” — audio{' + cover' if cover else ''} saved in the songs folder. "
                    "Next: paste the lyrics.")

    def _yt_fail(self, msg):
        self.yt_busy = False
        self.btn_yt.state(["!disabled"])
        self.yt_bar["value"] = 0
        self.status("Couldn't get that YouTube video.")
        hint = ""
        low = msg.lower()
        if "private" in low or "unavailable" in low:
            hint = "\n\nThis video is private, removed or blocked in your region."
        elif "sign in" in low or "age" in low:
            hint = "\n\nThis video needs a signed-in account (age-restricted or members-only)."
        else:
            hint = ("\n\nYouTube changes often. Run Setup.bat again to update the downloader, "
                    "then try once more.")
        messagebox.showerror("YouTube download failed", msg.strip()[-800:] + hint)

    def set_audio(self, p):
        self.v_audio.set(p)
        base = os.path.splitext(os.path.basename(p))[0]
        if not self.v_title.get():
            self.v_title.set(base)
        if not self.v_out.get():
            self.v_out.set(os.path.join(os.path.dirname(p), base + "_karaoke.mp4"))
        self.status("Loading song…")
        self.update_idletasks()
        try:
            self.player.load(p)
        except Exception as e:
            messagebox.showerror("Couldn't load the song", str(e))
            return
        self.seek.configure(to=max(1.0, self.player.duration))
        self.status(f"Song loaded ({fmt(self.player.duration)}). Next: paste the lyrics.")
        self.dirty = True

    def pick_bg(self):
        p = filedialog.askopenfilename(title="Choose a background", filetypes=BG_TYPES)
        if p:
            self.v_bg.set(p)
            self.dirty = True

    def pick_out(self):
        init = self.v_out.get()
        p = filedialog.asksaveasfilename(title="Save karaoke video as", defaultextension=".mp4",
                                         initialdir=os.path.dirname(init) if init else None,
                                         initialfile=os.path.basename(init) if init else "karaoke.mp4",
                                         filetypes=[("MP4 video", "*.mp4")])
        if p:
            self.v_out.set(p)

    def pick_color(self, s):
        c = colorchooser.askcolor(color=self.colors[s], title="Choose colour")[1]
        if c:
            self.colors[s] = c.upper()
            self._paint_color_btns()
            self._tag_colors()
            self._draw_preview(force=True)
            self.dirty = True

    def _font_changed(self):
        f = self.v_font.get().strip() or karaoke.FONT
        ttk.Style(self).configure("Treeview", font=(f, 12))
        self.edit_entry.configure(font=(f, 12))
        self._draw_preview(force=True)

    # ---------------------------------------------------------------- table
    def refresh(self, select=None, see=True):
        sel = select if select is not None else self.selected_indices()
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.rows):
            if r["text"]:
                singer = {"M": "Male", "F": "Female", "D": "Both"}[r["singer"]]
                if re.search(r"\{[MFD]\}", r["text"]):
                    singer += " +"
                vals = (i + 1, fmt(r["t"]), singer, r["text"])
                tag = r["singer"]
            else:
                vals = (i + 1, fmt(r["t"]), "", "♪  music break  ♪")
                tag = "B"
            self.tree.insert("", "end", iid=str(i), values=vals, tags=(tag,))
        sel = [i for i in sel if 0 <= i < len(self.rows)]
        if sel:
            self.tree.selection_set([str(i) for i in sel])
            self.tree.focus(str(sel[0]))
            if see:
                self.tree.see(str(sel[0]))
        self._now_row = None
        self._draw_preview(force=True)

    def selected_indices(self):
        return sorted(int(i) for i in self.tree.selection())

    def _load_editor(self):
        sel = self.selected_indices()
        if not sel:
            return
        r = self.rows[sel[0]]
        self.v_edit_text.set(r["text"])
        self.v_edit_time.set(fmt(r["t"]) if r["t"] is not None else "")
        self.v_edit_singer.set(r["singer"])

    @staticmethod
    def _set_time(r, t):
        """Change a line's start time; word timings (if any) move with it."""
        if r.get("words") and r["t"] is not None and t is not None:
            d = t - r["t"]
            r["words"] = [None if w is None else max(0.0, round(w + d, 2)) for w in r["words"]]
        elif t is None:
            r["words"] = None
        r["t"] = t

    def apply_edit(self):
        sel = self.selected_indices()
        if not sel:
            return
        i = sel[0]
        r = self.rows[i]
        new_text = self.v_edit_text.get().strip()
        new_text, stamped = split_stamps(new_text)
        if stamped:
            r["words"] = stamped
        elif r.get("words") and len(word_tokens(new_text)) != len(word_tokens(r["text"])):
            r["words"] = None   # words changed, per-word timing no longer fits
        r["text"] = new_text
        r["singer"] = self.v_edit_singer.get()
        tt = self.v_edit_time.get().strip()
        if tt and tt != "--:--.--":
            try:
                self._set_time(r, parse_time(tt))
            except ValueError:
                messagebox.showerror("Time", "Start time should look like 01:23.45")
        elif not tt:
            self._set_time(r, None)
        self.dirty = True
        self.refresh([i])
        self.tree.focus_set()

    def paste_dialog(self):
        d = tk.Toplevel(self)
        d.title("Paste lyrics")
        d.configure(bg=PANEL)
        d.transient(self)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        d.geometry(f"{min(900, int(sw * 0.6))}x{min(760, int(sh * 0.8))}")
        d.minsize(520, 420)
        fr = ttk.Frame(d, padding=14)
        fr.pack(fill="both", expand=True)

        # Buttons and options are packed at the BOTTOM first, so they always stay visible
        # (on scaled / high-DPI screens the text box would otherwise push them out of the window).
        btns = ttk.Frame(fr)
        btns.pack(side="bottom", fill="x", pady=(12, 0))
        opt = ttk.Frame(fr)
        opt.pack(side="bottom", fill="x", pady=(10, 0))

        ttk.Label(fr, text="Paste the song lyrics — one line per lyric line.", style="H.TLabel").pack(anchor="w")
        ttk.Label(fr, text="Optional: start a line with M:, F: or D: to set who sings it. "
                           "An empty line becomes a music break. Synced lyrics with [mm:ss.xx] "
                           "times are kept as they are.", style="Muted.TLabel",
                  wraplength=640, justify="left").pack(anchor="w", pady=(2, 8))
        tf = ttk.Frame(fr)
        tf.pack(fill="both", expand=True)
        txt = tk.Text(tf, wrap="word", bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat",
                      font=(self.v_font.get(), 13), padx=10, pady=8, undo=True, height=10)
        sb = ttk.Scrollbar(tf, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.focus_set()

        ttk.Label(opt, text="Lines without a tag are sung by:", style="Muted.TLabel").pack(side="left")
        for s_, n in (("M", "Male"), ("F", "Female"), ("D", "Both")):
            ttk.Radiobutton(opt, text=n, value=s_, variable=self.v_default_singer).pack(side="left", padx=(8, 0))
        v_breaks = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Empty lines = music break", variable=v_breaks).pack(side="right")

        v_count = tk.StringVar(value="0 lines")

        def count(_e=None):
            n = sum(1 for ln in txt.get("1.0", "end").splitlines() if ln.strip())
            v_count.set(f"{n} line{'s' if n != 1 else ''}")
            txt.edit_modified(False)

        txt.bind("<<Modified>>", count)

        def paste_clip():
            try:
                clip = self.clipboard_get()
            except tk.TclError:
                messagebox.showinfo("Paste", "The clipboard is empty — copy the lyrics first.", parent=d)
                return
            txt.insert("insert", clip)
            count()

        def go(replace):
            raw = txt.get("1.0", "end")
            dlog(f"PASTE go(replace={replace}) chars={len(raw.strip())} "
                 f"nonblank_lines={sum(1 for x in raw.splitlines() if x.strip())} first={raw.strip()[:12]!r}")
            try:
                new, meta, timed = parse_lyric_text(raw, self.v_default_singer.get(), v_breaks.get())
            except Exception as e:
                import traceback
                dlog("PASTE parse ERROR " + traceback.format_exc())
                messagebox.showerror("Paste lyrics", f"Couldn't read the lyrics:\n{e}", parent=d)
                return
            dlog(f"PASTE parsed rows={len(new)} text_rows={sum(1 for r in new if r['text'])} timed={timed}")
            if not any(r["text"] for r in new):
                messagebox.showinfo("Paste lyrics", "Paste or type the lyrics in the box first.", parent=d)
                return
            for k, var in (("title", self.v_title), ("artist", self.v_artist)):
                if meta.get(k) and (replace or not var.get()):
                    var.set(meta[k])
            if replace:
                self.rows = new
                first = 0
            else:
                first = len(self.rows)
                self.rows.extend(new)
            self.dirty = True
            try:
                self.refresh([first])
            except Exception:
                import traceback
                dlog("PASTE refresh ERROR " + traceback.format_exc())
                raise
            dlog(f"PASTE done: app rows={len(self.rows)} table_items={len(self.tree.get_children())}")
            d.destroy()
            self.tree.focus_set()
            n = sum(1 for r in new if r["text"])
            if timed:
                self.status(f"{n} timed lines added. Press Play to check them in the preview.")
            else:
                self.status(f"{n} lines added. Click ✨ Auto-sync, or press Play and tap SPACE as each line starts.")

        def close():
            dlog(f"PASTE window closed with {len(txt.get('1.0', 'end').strip())} chars")
            if txt.get("1.0", "end").strip():
                ans = messagebox.askyesnocancel("Paste lyrics", "Add these lyrics before closing?", parent=d)
                if ans is None:
                    return
                if ans:
                    go(not any(r["text"] for r in self.rows) or True)
                    return
            d.destroy()

        ttk.Button(btns, text="Paste from clipboard", command=paste_clip).pack(side="left")
        ttk.Label(btns, textvariable=v_count, style="Muted.TLabel").pack(side="left", padx=(10, 0))
        ttk.Button(btns, text="Use these lyrics (replace all)", style="Accent.TButton",
                   command=lambda: go(True)).pack(side="right")
        ttk.Button(btns, text="Add to end", command=lambda: go(False)).pack(side="right", padx=(0, 8))
        ttk.Button(btns, text="Cancel", command=lambda: (dlog("PASTE cancel"), d.destroy())).pack(
            side="right", padx=(0, 8))
        d.bind("<Control-Return>", lambda e: (go(True), "break")[1])
        d.protocol("WM_DELETE_WINDOW", close)
        self.after(10, lambda: self._center(d))

        def _log_geom():
            try:
                b = [c for c in btns.winfo_children()]
                vis = [(c.cget("text") if "text" in c.keys() else "?", c.winfo_ismapped(),
                        c.winfo_rooty() + c.winfo_height() <= d.winfo_rooty() + d.winfo_height()) for c in b]
                dlog(f"PASTE dialog size={d.winfo_width()}x{d.winfo_height()} buttons(mapped,inside)={vis}")
            except Exception as e:
                dlog(f"PASTE geom-log error {e!r}")
        d.after(600, _log_geom)
        dlog("PASTE dialog opened")

    def import_lyrics(self):
        p = filedialog.askopenfilename(title="Open a lyrics file",
                                       filetypes=[("Lyrics", "*.lrc *.txt *.srt"), ("All files", "*.*")])
        if not p:
            return
        raw = None
        for enc in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                with open(p, encoding=enc) as f:
                    raw = f.read()
                break
            except (UnicodeError, UnicodeDecodeError):
                continue
        if raw is None:
            messagebox.showerror("Lyrics file", "Couldn't read that file. Save it as UTF-8 and try again.")
            return
        rows, meta, timed = parse_lyric_text(raw, self.v_default_singer.get())
        if not rows:
            messagebox.showerror("Lyrics file", "No lyric lines found in that file.")
            return
        if any(r["text"] for r in self.rows) and not messagebox.askyesno(
                "Replace lyrics", "Replace the current lyrics with this file?"):
            return
        self.rows = rows
        for k, var in (("title", self.v_title), ("artist", self.v_artist)):
            if meta.get(k):
                var.set(meta[k])
        self.dirty = True
        self.refresh([0])
        n = sum(1 for r in rows if r["text"])
        if timed:
            self.status(f"Loaded {n} already-synced lines. Press Play to check them; set M / F / D singers.")
        else:
            self.status(f"Loaded {n} lines (no timing). Click ✨ Auto-sync, or tap them in with SPACE.")

    # ---------------------------------------------------------------- auto-sync
    def _autosync_ready(self):
        import importlib.util
        return (importlib.util.find_spec("stable_whisper") is not None,
                importlib.util.find_spec("demucs") is not None)

    def auto_sync(self):
        if self.busy or getattr(self, "sync_proc", None):
            return
        lines = [(i, r) for i, r in enumerate(self.rows) if r["text"]]
        if not lines:
            messagebox.showinfo("Auto-sync", "Add the lyrics first (Paste lyrics… or Open lyrics file…).")
            return
        audio = self.v_audio.get().strip()
        if not audio or not os.path.exists(audio):
            messagebox.showinfo("Auto-sync", "Choose the song first (step 1).")
            return
        have_sw, have_demucs = self._autosync_ready()
        if not have_sw:
            dlog("AUTOSYNC not installed (stable_whisper missing)")
            if messagebox.askyesno(
                    "Auto-sync needs a one-time install",
                    "Auto-sync uses a speech-recognition model that listens to the song. "
                    "It isn't installed yet.\n\n"
                    "Install it now? A black window will open and download about 2–3 GB "
                    "(10–30 minutes). When it says 'All done', close Karaoke Studio, "
                    "open it again, and click Auto-sync."):
                bat = os.path.join(HERE, "Setup AutoSync.bat")
                try:
                    if IS_WIN:
                        os.startfile(bat)  # noqa
                    else:
                        subprocess.Popen(["sh", bat])
                except Exception as e:
                    messagebox.showerror("Auto-sync", f"Couldn't start the installer:\n{e}\n\n"
                                                      f"Double-click 'Setup AutoSync.bat' in {HERE}")
            return
        dlog(f"AUTOSYNC ready demucs={have_demucs}")

        d = tk.Toplevel(self)
        d.title("Auto-sync lyrics")
        d.configure(bg=PANEL)
        d.transient(self)
        d.resizable(False, False)
        fr = ttk.Frame(d, padding=16)
        fr.pack(fill="both", expand=True)
        ttk.Label(fr, text="Auto-sync the lyrics to the song", style="H.TLabel").pack(anchor="w")
        ttk.Label(fr, text="The model listens to the song and finds when each line — and each word — is sung. "
                           "Check the result afterwards and fix any line with Play from line and −0.1 / +0.1.",
                  style="Muted.TLabel", wraplength=460, justify="left").pack(anchor="w", pady=(4, 12))
        g = ttk.Frame(fr)
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        all_text = " ".join(r["text"] for _, r in lines)
        guess = guess_lang(all_text)
        names = [n for n, _ in LANGS]
        v_lang = tk.StringVar(value=next((n for n, c in LANGS if c == guess), names[0]))
        ttk.Label(g, text="Language", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Combobox(g, textvariable=v_lang, values=names, state="readonly", width=24).grid(
            row=0, column=1, sticky="w", padx=(10, 0), pady=4)
        latin = guess == "en"
        if latin:
            ttk.Label(g, text="Lyrics in English letters (e.g. Tanglish)? Try the song's language first, "
                              "then English.", style="Muted.TLabel", wraplength=330).grid(
                row=1, column=1, sticky="w", padx=(10, 0))
        ttk.Label(g, text="Accuracy", style="Muted.TLabel").grid(row=2, column=0, sticky="nw", pady=(10, 4))
        v_model = tk.StringVar(value="medium")
        mf = ttk.Frame(g)
        mf.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(10, 4))
        for val, label in (("small", "Fast (small model, 0.5 GB)"),
                           ("medium", "Better (medium model, 1.5 GB) — recommended"),
                           ("large-v3", "Best (large model, 3 GB, slow)")):
            ttk.Radiobutton(mf, text=label, value=val, variable=v_model).pack(anchor="w")
        v_voc = tk.BooleanVar(value=have_demucs)
        cb = ttk.Checkbutton(fr, text="Separate the singing from the music first (more accurate for songs, slower)",
                             variable=v_voc)
        cb.pack(anchor="w", pady=(12, 0))
        if not have_demucs:
            cb.state(["disabled"])
            ttk.Label(fr, text="(needs demucs — run 'Setup AutoSync.bat' again to add it)",
                      style="Muted.TLabel").pack(anchor="w")
        ttk.Label(fr, text=f"{len(lines)} lines · song {fmt(self.player.duration)}. On a normal PC this takes "
                           "a few minutes; the first run also downloads the model.",
                  style="Muted.TLabel", wraplength=460, justify="left").pack(anchor="w", pady=(10, 0))
        btns = ttk.Frame(fr)
        btns.pack(fill="x", pady=(14, 0))

        def start():
            lang = dict(LANGS).get(v_lang.get()) or guess
            d.destroy()
            self._run_autosync(audio, lines, lang, v_model.get(), v_voc.get())

        ttk.Button(btns, text="Start auto-sync", style="Accent.TButton", command=start).pack(side="right")
        ttk.Button(btns, text="Cancel", command=d.destroy).pack(side="right", padx=(0, 8))
        self._center(d)
        d.grab_set()

    def _python_exe(self):
        exe = sys.executable
        if exe.lower().endswith("pythonw.exe"):
            alt = exe[:-len("pythonw.exe")] + "python.exe"
            if os.path.exists(alt):
                exe = alt
        return exe

    def _run_autosync(self, audio, lines, lang, model, vocals):
        import json
        work = tempfile.mkdtemp(prefix="ksync_")
        lj = os.path.join(work, "lines.json")
        outj = os.path.join(work, "result.json")
        clean = [re.sub(r"\s+", " ", re.sub(r"\{[MFDmfd]\}", " ", r["text"])).strip() for _, r in lines]
        with open(lj, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False)
        cmd = [self._python_exe(), os.path.join(HERE, "autosync.py"), audio, lj, "--model", model,
               "--models-dir", os.path.join(HERE, "models"), "--out", outj]
        if lang:
            cmd += ["--lang", lang]
        if vocals:
            cmd.append("--vocals")

        # progress window
        w = tk.Toplevel(self)
        w.title("Auto-syncing…")
        w.configure(bg=PANEL)
        w.transient(self)
        w.resizable(False, False)
        fr = ttk.Frame(w, padding=18)
        fr.pack(fill="both", expand=True)
        v_stage = tk.StringVar(value="Starting…")
        ttk.Label(fr, text="✨ Auto-sync", style="H.TLabel").pack(anchor="w")
        ttk.Label(fr, textvariable=v_stage, style="Muted.TLabel", wraplength=440,
                  justify="left").pack(anchor="w", pady=(6, 10))
        bar = ttk.Progressbar(fr, mode="indeterminate", length=440, maximum=1000)
        bar.pack(fill="x")
        bar.start(12)
        v_el = tk.StringVar(value="")
        ttk.Label(fr, textvariable=v_el, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        t0 = time.time()
        state = {"stage": "", "pct": None, "err": "", "done": False, "tail": ""}

        def cancel():
            p = getattr(self, "sync_proc", None)
            if p and p.poll() is None:
                p.kill()
            state["err"] = state["err"] or "Cancelled."

        ttk.Button(fr, text="Cancel", command=cancel).pack(anchor="e", pady=(12, 0))
        w.protocol("WM_DELETE_WINDOW", cancel)
        self.btn_sync.state(["disabled"])

        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        dlog(f"AUTOSYNC start lang={lang} model={model} vocals={vocals} lines={len(lines)} cmd={cmd[0]}")
        try:
            self.sync_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                              cwd=HERE, creationflags=NO_WINDOW)
        except Exception as e:
            w.destroy()
            self.btn_sync.state(["!disabled"])
            self.sync_proc = None
            messagebox.showerror("Auto-sync", f"Couldn't start auto-sync:\n{e}")
            return

        def read_out():
            for raw in self.sync_proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("STAGE "):
                    state["stage"] = line[6:]
                    state["pct"] = None
                elif line.startswith("ERROR "):
                    state["err"] = line[6:]
                elif line.startswith("DONE"):
                    state["done"] = True

        def read_err():
            buf = b""
            while True:
                ch = self.sync_proc.stderr.read(64)
                if not ch:
                    break
                buf = (buf + ch)[-4000:]
                txt = buf.decode("utf-8", "replace")
                m = re.findall(r"(\d{1,3})%\|", txt)
                if m:
                    state["pct"] = min(100, int(m[-1]))
                state["tail"] = txt

        threading.Thread(target=read_out, daemon=True).start()
        threading.Thread(target=read_err, daemon=True).start()

        def poll():
            if state["stage"]:
                v_stage.set(state["stage"])
            if state["pct"] is not None:
                if str(bar.cget("mode")) != "determinate":
                    bar.stop()
                    bar.configure(mode="determinate")
                bar["value"] = state["pct"] * 10
            el = int(time.time() - t0)
            v_el.set(f"{el // 60}:{el % 60:02d} elapsed" + (f" · {state['pct']}%" if state["pct"] is not None else ""))
            if self.sync_proc.poll() is None:
                w.after(250, poll)
                return
            code = self.sync_proc.returncode
            self.sync_proc = None
            bar.stop()
            w.destroy()
            self.btn_sync.state(["!disabled"])
            if state["done"] and code == 0 and os.path.exists(outj):
                with open(outj, encoding="utf-8") as f:
                    words = json.load(f).get("words", [])
                dlog(f"AUTOSYNC ok words={len(words)} elapsed={int(time.time() - t0)}s")
                shutil.rmtree(work, ignore_errors=True)
                self._apply_alignment(lines, words)
            else:
                shutil.rmtree(work, ignore_errors=True)
                dlog(f"AUTOSYNC failed code={code} err={state['err']!r} tail={state['tail'][-1500:]!r}")
                if state["err"] == "Cancelled.":
                    self.status("Auto-sync cancelled.")
                    return
                msg = state["err"] or ("Auto-sync stopped unexpectedly.\n\n" +
                                       re.sub(r"\s+\n", "\n", state["tail"])[-700:])
                self.status("Auto-sync didn't finish.")
                messagebox.showerror("Auto-sync", msg)

        w.after(250, poll)
        self._center(w)
        w.grab_set()

    def _apply_alignment(self, lines, words):
        if not words:
            messagebox.showerror("Auto-sync", "The model didn't return any timings. Check the language setting "
                                              "and that the lyrics match this song.")
            return
        texts = [r["text"] for _, r in lines]
        mapped = map_alignment(texts, words)
        synced = []
        for (idx, r), (st, en, wt) in zip(lines, mapped):
            r["t"] = st
            r["words"] = wt if len(wt) == len(word_tokens(r["text"])) else None
            r["_end"] = en
            synced.append(r)
        # rebuild music breaks from the gaps between sung lines
        new_rows = []
        for k, r in enumerate(synced):
            new_rows.append(r)
            nxt = synced[k + 1]["t"] if k + 1 < len(synced) else None
            end = r.pop("_end")
            if nxt is None or nxt - end > 4.0:
                new_rows.append({"t": round(end + 0.4, 2), "singer": r["singer"], "text": "", "words": None})
        self.rows = new_rows
        self.dirty = True
        self.refresh([0])
        self.status(f"✨ Auto-synced {len(synced)} lines. Press Play to check; fix any line with "
                    "Play from line and −0.1 / +0.1.")
        messagebox.showinfo("Auto-sync done",
                            f"{len(synced)} lines are now timed (word by word).\n\n"
                            "Press Play and watch the preview. If a line is off, select it and use "
                            "Play from line, then −0.1 / +0.1 or tap SPACE at the right moment.\n\n"
                            "Also set who sings each line with M / F / D.")

    def add_line(self):
        sel = self.selected_indices()
        i = sel[-1] + 1 if sel else len(self.rows)
        singer = self.rows[sel[-1]]["singer"] if sel else self.v_default_singer.get()
        self.rows.insert(i, {"t": None, "singer": singer, "text": "new line", "words": None})
        self.dirty = True
        self.refresh([i])
        self.edit_entry.focus_set()
        self.edit_entry.select_range(0, "end")

    def add_break(self):
        sel = self.selected_indices()
        i = sel[-1] + 1 if sel else len(self.rows)
        self.rows.insert(i, {"t": None, "singer": "M", "text": "", "words": None})
        self.dirty = True
        self.refresh([i])

    def delete_rows(self):
        sel = self.selected_indices()
        if not sel:
            return
        for i in reversed(sel):
            del self.rows[i]
        self.dirty = True
        self.refresh([min(sel[0], len(self.rows) - 1)])

    def move_row(self, d):
        sel = self.selected_indices()
        if len(sel) != 1:
            return
        i, j = sel[0], sel[0] + d
        if 0 <= j < len(self.rows):
            self.rows[i], self.rows[j] = self.rows[j], self.rows[i]
            self.dirty = True
            self.refresh([j])

    def clear_times(self):
        if self.rows and messagebox.askyesno("Clear times", "Remove all start times so you can re-tap?"):
            for r in self.rows:
                r["t"] = None
                r["words"] = None
            self.dirty = True
            self.refresh([0])

    def set_singer(self, s):
        sel = self.selected_indices()
        if not sel:
            return
        for i in sel:
            if self.rows[i]["text"]:
                self.rows[i]["singer"] = s
        self.dirty = True
        self.refresh(sel)
        self._load_editor()

    def nudge_time(self, d):
        sel = self.selected_indices()
        for i in sel:
            if self.rows[i]["t"] is not None:
                self._set_time(self.rows[i], max(0.0, round(self.rows[i]["t"] + d, 2)))
        self.dirty = True
        self.refresh(sel)
        self._load_editor()

    # ---------------------------------------------------------------- tapping
    def tap(self):
        if not self.rows:
            return
        if not self.player.playing:
            self.status("Press Play first, then tap SPACE as each line starts.")
            return
        sel = self.selected_indices()
        i = sel[0] if sel else 0
        t = round(max(0.0, self.player.position() - 0.08), 2)   # small reaction-time correction
        self._set_time(self.rows[i], t)
        self.dirty = True
        nxt = min(i + 1, len(self.rows) - 1)
        self.refresh([nxt])
        self._load_editor()
        self._flash_tap()

    def _flash_tap(self):
        self.btn_tap.state(["pressed"])
        self.after(120, lambda: self.btn_tap.state(["!pressed"]))

    # ---------------------------------------------------------------- playback
    def toggle_play(self):
        if not self.player.ok:
            messagebox.showinfo("Playback", "Install pygame (run Setup.bat) to play the song in the app.")
            return
        if not self.player.path:
            self.pick_audio()
            return
        if self.player.playing:
            self.player.pause()
        else:
            self.player.play()
        self.btn_play.configure(text="❚❚  Pause" if self.player.playing else "▶  Play")

    def nudge_play(self, d):
        if self.player.path:
            self.player.seek(self.player.position() + d)

    def play_from_selected(self):
        sel = self.selected_indices()
        if not sel or not self.player.path:
            return
        t = self.rows[sel[0]]["t"]
        if t is None:
            prev = [r["t"] for r in self.rows[:sel[0]] if r["t"] is not None]
            t = prev[-1] if prev else 0.0
        self.player.seek(max(0.0, t - 2.0))
        if not self.player.playing:
            self.toggle_play()

    def _on_seek_drag(self, v):
        if self._seek_drag:
            self.v_time.set(f"{fmt(float(v))} / {fmt(self.player.duration)}")

    def _on_seek_release(self, e):
        self._seek_drag = False
        self.player.seek(float(self.seek.get()))

    def _tick(self):
        pos = self.player.position()
        if self.player.playing and self.player.duration and pos >= self.player.duration:
            self.player.pause()
            self.player.seek(0)
            self.btn_play.configure(text="▶  Play")
        if not self._seek_drag:
            self.seek.set(pos)
            self.v_time.set(f"{fmt(pos)} / {fmt(self.player.duration)}")
        self._draw_preview()
        self._poll_jobs()
        self.after(40, self._tick)

    # ---------------------------------------------------------------- live preview
    def _current_index(self, pos):
        cur = None
        for i, r in enumerate(self.rows):
            if r["t"] is not None and r["t"] <= pos:
                cur = i
        return cur

    def _draw_preview(self, force=False):
        pos = self.player.position()
        cur = self._current_index(pos)
        key = (cur, self.canvas.winfo_width(), self.v_font.get(), tuple(self.colors.values()), len(self.rows))
        if not force and key == getattr(self, "_pv_key", None):
            return
        self._pv_key = key
        c = self.canvas
        c.delete("all")
        w = max(200, c.winfo_width())
        font = self.v_font.get() or karaoke.FONT
        if not self.rows:
            c.create_text(w / 2, 85, text="Your lyrics will preview here while the song plays",
                          fill=MUTED, font=(font, 14))
            return
        # highlight the singing line in the table
        if cur != getattr(self, "_now_row", None):
            old = getattr(self, "_now_row", None)
            if old is not None and self.tree.exists(str(old)):
                tags = [t for t in self.tree.item(str(old), "tags") if t != "now"]
                self.tree.item(str(old), tags=tags)
            if cur is not None and self.tree.exists(str(cur)):
                self.tree.item(str(cur), tags=list(self.tree.item(str(cur), "tags")) + ["now"])
            self._now_row = cur
        start = 0 if cur is None else cur
        shown = []
        i = start
        while i < len(self.rows) and len(shown) < 3:
            shown.append(self.rows[i])
            i += 1
        ys, sizes = (48, 104, 142), (24, 16, 16)
        for k, r in enumerate(shown):
            text = re.sub(r"\{[MFD]\}|<\d+:\d+(?:\.\d+)?>", "", r["text"]).strip()
            text = re.sub(r"\s+", " ", text)
            if not text:
                text, col = "♪  ♪  ♪", MUTED
            else:
                col = self.colors[r["singer"]]
                if k > 0 or cur is None:
                    col = karaoke.blend(col, "#FFFFFF", 0.35)
            weight = "bold" if k == 0 else "normal"
            c.create_text(w / 2, ys[k], text=text, fill=col, font=(font, sizes[k], weight), width=w - 40)
        legend = [("M", "Male"), ("F", "Female"), ("D", "Both")]
        x = w - 12
        for s, n in reversed(legend):
            t_id = c.create_text(x, 14, text=n, anchor="e", fill=TEXT, font=(self.base_font[0], 9))
            bx = c.bbox(t_id)
            c.create_oval(bx[0] - 14, 9, bx[0] - 4, 19, fill=self.colors[s], outline="")
            x = bx[0] - 24

    # ---------------------------------------------------------------- project I/O
    def to_lrc(self):
        out = []
        for k, v in (("title", self.v_title), ("artist", self.v_artist)):
            if v.get().strip():
                out.append(f"{k}: {v.get().strip()}")
        out.append(f"audio: {self.v_audio.get()}")
        if self.v_bg.get():
            out.append(f"background: {self.v_bg.get()}")
        out.append(f"font: {self.v_font.get()}")
        out.append("colors: " + ",".join(f"{s}={c}" for s, c in self.colors.items()))
        if self.v_out.get():
            out.append(f"output: {self.v_out.get()}")
        if self.v_vocals.get():
            out.append("reduce_vocals: yes")
        out.append(f"blur_cover: {'yes' if self.v_blur.get() else 'no'}")
        out.append("")
        last_singer = None
        for r in self.rows:
            text = with_stamps(r["text"], r.get("words"))
            if r["t"] is None:
                out.append(f"# (not timed yet) {r['singer']}: {text}" if r["text"] else "# (untimed break)")
                continue
            tag = f"[{fmt(r['t'])}]"
            if not r["text"]:
                out.append(tag)
            else:
                out.append(f"{tag} {r['singer']}: {text}")
                last_singer = r["singer"]
        return "\n".join(out) + "\n"

    def from_lrc(self, path):
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        rows, singer = [], "M"
        settings = {}
        for ln in lines:
            s = ln.strip()
            if not s:
                continue
            m = re.match(r"^#\s*\(not timed yet\)\s*([MFD])\s*:\s*(.*)$", s)
            if m:
                text, words = split_stamps(m.group(2))
                rows.append({"t": None, "singer": m.group(1), "text": text, "words": words})
                continue
            if s.startswith("# (untimed break)"):
                rows.append({"t": None, "singer": singer, "text": "", "words": None})
                continue
            if s.startswith("#"):
                continue
            m = TS_RE.match(s)
            if m:
                t = int(m.group(1)) * 60 + float(m.group(2))
                text = m.group(3).strip()
                sm = re.match(r"^([MFD])\s*:\s*(.*)$", text, re.I)
                if sm:
                    singer, text = sm.group(1).upper(), sm.group(2).strip()
                text, words = split_stamps(text)
                rows.append({"t": t, "singer": singer, "text": text, "words": words})
                continue
            m = re.match(r"^\[?(\w+)\s*:\s*(.*?)\]?$", s)
            if m:
                settings[m.group(1).lower()] = m.group(2).strip()
        # A plain (non-project) lyrics file: read it the general way
        if not rows or not any(k in settings for k in ("audio", "font", "colors")):
            with open(path, encoding="utf-8-sig") as f:
                prow, meta, _ = parse_lyric_text(f.read(), self.v_default_singer.get())
            if prow:
                rows = prow
                for k in ("title", "artist"):
                    if meta.get(k):
                        settings[k] = meta[k]
        return rows, settings

    def new_project(self):
        if not self._confirm_discard():
            return
        self.player.stop()
        self.rows = []
        self.project_path = None
        for v in (self.v_audio, self.v_bg, self.v_title, self.v_artist, self.v_out):
            v.set("")
        self.v_vocals.set(False)
        self.player.path = None
        self.player.duration = 0.0
        self.dirty = False
        self.refresh([])
        self.status("New project. Choose the song to begin.")

    def open_project(self):
        if not self._confirm_discard():
            return
        p = filedialog.askopenfilename(title="Open lyrics / project",
                                       filetypes=[("Lyrics file", "*.lrc *.txt"), ("All files", "*.*")])
        if not p:
            return
        try:
            rows, st = self.from_lrc(p)
        except Exception as e:
            messagebox.showerror("Open", f"Couldn't read the file:\n{e}")
            return
        self.rows = rows
        self.project_path = p
        self.v_title.set(st.get("title", st.get("ti", "")))
        self.v_artist.set(st.get("artist", st.get("ar", "")))
        self.v_bg.set(st.get("background", ""))
        self.v_out.set(st.get("output", ""))
        self.v_vocals.set(st.get("reduce_vocals", "") == "yes")
        self.v_blur.set(st.get("blur_cover", "yes") != "no")
        if st.get("font"):
            self.v_font.set(st["font"])
            self._font_changed()
        if st.get("colors"):
            for part in st["colors"].split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    if k.strip() in self.colors:
                        self.colors[k.strip()] = v.strip()
            self._paint_color_btns()
            self._tag_colors()
        audio = st.get("audio", "")
        if not audio or not os.path.exists(audio):
            base = os.path.splitext(p)[0]
            for ext in (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".mp4"):
                if os.path.exists(base + ext):
                    audio = base + ext
                    break
        if audio and os.path.exists(audio):
            self.set_audio(audio)
        self.dirty = False
        self.refresh([0])
        self.status(f"Opened {os.path.basename(p)} — {sum(1 for r in rows if r['text'])} lines.")

    def save_project(self, ask=False):
        p = self.project_path
        if ask or not p:
            base = self.v_title.get().strip() or "mysong"
            init_dir = os.path.dirname(self.v_audio.get()) if self.v_audio.get() else None
            if self.v_out.get():
                init_dir = os.path.dirname(self.v_out.get())
            p = filedialog.asksaveasfilename(title="Save project (lyrics + settings)",
                                             defaultextension=".lrc", initialfile=base + ".lrc",
                                             initialdir=init_dir,
                                             filetypes=[("Lyrics file", "*.lrc")])
            if not p:
                return False
        with open(p, "w", encoding="utf-8") as f:
            f.write(self.to_lrc())
        self.project_path = p
        self.dirty = False
        self.status(f"Saved {os.path.basename(p)}")
        return True

    def _confirm_discard(self):
        if not self.dirty or not self.rows:
            return True
        r = messagebox.askyesnocancel("Unsaved changes", "Save your lyrics and timing first?")
        if r is None:
            return False
        if r:
            return self.save_project()
        return True

    # ---------------------------------------------------------------- rendering
    def _prepare(self):
        if not shutil.which("ffmpeg"):
            messagebox.showerror("ffmpeg missing", "ffmpeg isn't installed. Run Setup.bat first.")
            return None
        audio = self.v_audio.get().strip()
        if not audio or not os.path.exists(audio):
            messagebox.showerror("Song", "Choose the song file first (step 1).")
            return None
        timed = [r for r in self.rows if r["t"] is not None]
        if not any(r["text"] for r in timed):
            messagebox.showerror("Timing", "No lines have a start time yet.\n\nPress Play and tap SPACE "
                                           "as each line starts.")
            return None
        untimed = sum(1 for r in self.rows if r["t"] is None and r["text"])
        if untimed and not messagebox.askyesno(
                "Some lines not timed", f"{untimed} line(s) have no start time and will be left out. Continue?"):
            return None
        karaoke.FONT = self.v_font.get().strip() or karaoke.FONT
        karaoke.SINGER_COLORS.update(self.colors)
        tmp = tempfile.NamedTemporaryFile("w", suffix=".lrc", delete=False, encoding="utf-8")
        tmp.write(self.to_lrc())
        tmp.close()
        try:
            meta, lines = karaoke.parse_lyrics(tmp.name)
        finally:
            os.unlink(tmp.name)
        duration = karaoke.probe_duration(audio) or (lines[-1]["end"] + 4)
        ass = karaoke.build_ass(meta, lines, duration)
        return dict(ass=ass, audio=audio, bg=self.v_bg.get().strip() or None,
                    duration=duration, vocals=self.v_vocals.get(), blur=self.v_blur.get())

    def preview_clip(self):
        if self.busy:
            return
        job = self._prepare()
        if not job:
            return
        sel = self.selected_indices()
        start = 0.0
        if sel:
            prev = [r["t"] for r in self.rows[:sel[0] + 1] if r["t"] is not None]
            start = max(0.0, (prev[-1] if prev else 0.0) - 2.0)
        out = os.path.join(tempfile.gettempdir(), "karaoke_studio_preview.mp4")
        self._run(job, out, preview=20.0, start=start, open_after=True,
                  label=f"Making preview from {fmt(start)}…")

    def render_full(self):
        if self.busy:
            return
        job = self._prepare()
        if not job:
            return
        out = self.v_out.get().strip()
        if not out:
            self.pick_out()
            out = self.v_out.get().strip()
            if not out:
                return
        if not out.lower().endswith(".mp4"):
            out += ".mp4"
            self.v_out.set(out)
        if self.project_path or self.rows:
            try:
                if self.project_path:
                    self.save_project()
                else:
                    auto = os.path.splitext(out)[0] + ".lrc"
                    with open(auto, "w", encoding="utf-8") as f:
                        f.write(self.to_lrc())
                    self.project_path = auto
                    self.dirty = False
            except Exception:
                pass
        self._run(job, out, preview=None, start=0.0, open_after=False,
                  label="Making your karaoke video…")

    def _run(self, job, out, preview, start, open_after, label):
        self.busy = True
        for b in (self.btn_render, self.btn_preview):
            b.state(["disabled"])
        self.pbar["value"] = 0
        self.status(label)
        t0 = time.time()

        def work():
            try:
                karaoke.render(job["ass"], job["audio"], job["bg"], out, job["duration"],
                               preview_secs=preview, start=start, reduce_vocals=job["vocals"],
                               blur_bg=job["blur"],
                               progress=lambda f: self.jobs.put(("progress", f, t0)))
                self.jobs.put(("done", out, open_after, preview))
            except Exception as e:
                self.jobs.put(("error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _poll_jobs(self):
        try:
            while True:
                msg = self.jobs.get_nowait()
                if msg[0] == "yt_progress":
                    self.yt_bar["value"] = int(msg[1] * 1000)
                    self.status(f"Downloading audio… {int(msg[1] * 100)}%")
                    continue
                if msg[0] == "yt_status":
                    self.status(msg[1])
                    continue
                if msg[0] == "yt_done":
                    self._yt_finish(*msg[1:])
                    continue
                if msg[0] == "yt_error":
                    self._yt_fail(msg[1])
                    continue
                if msg[0] == "progress":
                    f, t0 = msg[1], msg[2]
                    self.pbar["value"] = int(f * 1000)
                    el = time.time() - t0
                    eta = f" · about {int(el / f - el)} s left" if f > 0.03 else ""
                    self.status(f"Rendering… {int(f * 100)}%{eta}")
                elif msg[0] == "done":
                    self.busy = False
                    for b in (self.btn_render, self.btn_preview):
                        b.state(["!disabled"])
                    out, open_after, preview = msg[1], msg[2], msg[3]
                    self.pbar["value"] = 1000
                    if preview:
                        self.status("Preview ready — playing it in your video player.")
                        open_path(out)
                    else:
                        self.last_output = out
                        self.btn_open.state(["!disabled"])
                        self.btn_folder.state(["!disabled"])
                        self.status(f"Done! Saved {out}")
                        if messagebox.askyesno("Karaoke video ready",
                                               f"Your video is ready:\n{out}\n\nOpen it now?"):
                            open_path(out)
                elif msg[0] == "error":
                    self.busy = False
                    for b in (self.btn_render, self.btn_preview):
                        b.state(["!disabled"])
                    self.pbar["value"] = 0
                    self.status("Something went wrong — see the message.")
                    messagebox.showerror("Render failed", msg[1])
        except queue.Empty:
            pass

    def open_output(self):
        if self.last_output and os.path.exists(self.last_output):
            open_path(self.last_output)

    def open_folder(self):
        if not self.last_output:
            return
        if IS_WIN:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.last_output)])
        else:
            open_path(os.path.dirname(self.last_output))

    # ---------------------------------------------------------------- misc
    def _center(self, win):
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = self.winfo_rootx() + (self.winfo_width() - w) // 2
        y = self.winfo_rooty() + (self.winfo_height() - h) // 3
        win.geometry(f"+{max(0, x)}+{max(0, y)}")

    def status(self, s):
        self.v_status.set(s)

    def on_close(self):
        if self.busy and not messagebox.askyesno("Still rendering", "A video is still being made. Quit anyway?"):
            return
        if not self._confirm_discard():
            return
        self.player.cleanup()
        self.destroy()


if __name__ == "__main__":
    if IS_WIN:
        try:
            import ctypes
            # own taskbar group, so Windows shows our icon instead of Python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("KaraokeStudio.App")
        except Exception:
            pass
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)   # crisp text on high-DPI screens
        except Exception:
            pass
    try:
        app = Studio()

        def report(exc, val, tb):
            import traceback
            msg = "".join(traceback.format_exception(exc, val, tb))
            with open(os.path.join(HERE, "karaoke_studio_error.log"), "a", encoding="utf-8") as f:
                f.write(msg + "\n")
            dlog("UNCAUGHT " + msg)
            messagebox.showerror("Something went wrong", f"{val}\n\n(Details saved to karaoke_studio_error.log)")

        app.report_callback_exception = report
        app.mainloop()
    except Exception:
        import traceback
        with open(os.path.join(HERE, "karaoke_studio_error.log"), "a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
        raise
