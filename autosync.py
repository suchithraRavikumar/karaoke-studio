#!/usr/bin/env python3
"""
autosync.py — line up plain lyrics with a song automatically.

Uses stable-ts (Whisper "forced alignment"): the model listens to the song and
finds when each word of the lyrics you supply is sung. Karaoke Studio runs this
in the background; you can also run it by hand:

  python autosync.py song.mp3 lines.json --lang ta --model medium --out result.json

lines.json is a JSON list of lyric lines (strings). The result JSON holds every
aligned word as [text, start, end]. Progress messages go to stdout as
"STAGE ...", "ERROR ..." and "DONE"; the model's progress bar goes to stderr.

One-time install: run "Setup AutoSync.bat" (pip install stable-ts demucs).
"""
import argparse
import json
import os
import sys

# Newer PyTorch refuses to load the Demucs separator's model files by default (used when
# "separate the singing" is on); they are the official research models, so allow it.
os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")


def say(kind, msg=""):
    print(f"{kind} {msg}".strip(), flush=True)


def main():
    ap = argparse.ArgumentParser(description="Auto-sync lyrics to a song")
    ap.add_argument("audio")
    ap.add_argument("lines_json")
    ap.add_argument("--lang", default=None, help="language code, e.g. ta, hi, te, kn, ml, en")
    ap.add_argument("--model", default="medium", help="small / medium / large-v3")
    ap.add_argument("--vocals", action="store_true", help="separate the voice from the music first (demucs)")
    ap.add_argument("--models-dir", default=None, help="where to keep downloaded models")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    if a.models_dir:
        os.makedirs(a.models_dir, exist_ok=True)
        os.environ.setdefault("TORCH_HOME", os.path.join(a.models_dir, "torch"))

    with open(a.lines_json, encoding="utf-8") as f:
        lines = [ln for ln in json.load(f) if ln.strip()]
    if not lines:
        say("ERROR", "No lyric lines to sync.")
        return 2

    try:
        import torch
        import stable_whisper
    except ImportError:
        say("ERROR", "Auto-sync isn't installed yet. Run 'Setup AutoSync.bat' in the app folder (one time).")
        return 2

    device = "cuda" if torch.cuda.is_available() else "cpu"
    say("STAGE", f"Loading the '{a.model}' speech model on {device.upper()} "
                 "(the first time, it downloads it — this can take a while)…")
    try:
        model = stable_whisper.load_model(a.model, device=device, download_root=a.models_dir)
    except TypeError:   # older stable-ts without download_root
        model = stable_whisper.load_model(a.model, device=device)

    kw = dict(language=a.lang, original_split=True, verbose=False)
    if a.vocals:
        kw["denoiser"] = "demucs"
        say("STAGE", "Separating the singing from the music, then matching the lyrics…")
    else:
        say("STAGE", "Listening to the song and matching the lyrics…")

    text = "\n".join(lines)
    try:
        result = model.align(a.audio, text, **kw)
    except TypeError:
        # very old stable-ts: no denoiser / original_split arguments
        kw.pop("denoiser", None)
        kw.pop("original_split", None)
        if a.vocals:
            kw["demucs"] = True
        result = model.align(a.audio, text, **kw)

    if result is None:
        say("ERROR", "The model couldn't match these lyrics to the song. Check the language and that the "
                     "lyrics are for this song.")
        return 3

    words = []
    for seg in result.segments:
        for w in seg.words:
            words.append([w.word, float(w.start), float(w.end)])
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"words": words}, f, ensure_ascii=False)
    say("DONE")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:  # report cleanly to the app
        say("ERROR", f"{type(e).__name__}: {e}")
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code or 0)   # skip interpreter shutdown (PyTorch can crash there on some Windows setups)
