#!/usr/bin/env python3
"""
separate.py — split a song into an instrumental (karaoke) track and a vocals track
with Demucs (AI source separation). Karaoke Studio runs this in the background.

  python separate.py song.mp3 --inst "song (instrumental).mp3" --vocals "song (vocals).mp3"

Audio is read and written with ffmpeg, so it works with any input ffmpeg can open
(mp3, m4a, wav, video files…). Progress: "STAGE …", "ERROR …", "DONE" on stdout,
the model's progress bar (percent) on stderr.
"""
import argparse
import os
import shutil
import subprocess
import sys

# Newer PyTorch refuses to load Demucs' model files by default; they are the official
# Meta/Facebook research models, so allow the classic loader.
os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")

NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


def say(kind, msg=""):
    print(f"{kind} {msg}".strip(), flush=True)


def decode(ffmpeg, src, sr):
    import numpy as np
    r = subprocess.run([ffmpeg, "-v", "error", "-i", src, "-vn", "-f", "f32le", "-ac", "2",
                        "-ar", str(sr), "-"], capture_output=True, creationflags=NO_WINDOW)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("ffmpeg couldn't read the song: " + r.stderr.decode("utf-8", "replace")[-400:])
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 2).T.copy()   # (2, n)


def encode(ffmpeg, data, sr, out):
    import numpy as np
    pcm = np.clip(data, -1.0, 1.0).T.astype(np.float32).tobytes()
    tmp = out + ".part.mp3"
    p = subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", "-",
                        "-c:a", "libmp3lame", "-b:a", "192k", tmp], input=pcm, capture_output=True,
                       creationflags=NO_WINDOW)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg couldn't save the result: " + p.stderr.decode("utf-8", "replace")[-400:])
    os.replace(tmp, out)


def main():
    ap = argparse.ArgumentParser(description="Remove vocals from a song (Demucs)")
    ap.add_argument("src")
    ap.add_argument("--inst", required=True, help="output instrumental mp3")
    ap.add_argument("--vocals", help="output vocals-only mp3 (optional)")
    ap.add_argument("--model", default="htdemucs")
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    a = ap.parse_args()

    if a.models_dir:
        os.makedirs(a.models_dir, exist_ok=True)
        os.environ.setdefault("TORCH_HOME", os.path.join(a.models_dir, "torch"))

    try:
        import numpy as np
        import torch
        from demucs.pretrained import get_model
        from demucs.apply import apply_model
    except ImportError as e:
        say("ERROR", f"The vocal remover isn't installed yet ({e.name}). Run 'Setup AI Tools.bat' in the app folder.")
        return 2

    device = "cuda" if torch.cuda.is_available() else "cpu"
    say("STAGE", "Loading the voice-separation model (the first time it downloads about 80 MB)…")
    model = get_model(a.model)
    model.eval()
    sr = int(getattr(model, "samplerate", 44100))

    say("STAGE", "Reading the song…")
    wav = decode(a.ffmpeg, a.src, sr)
    ref = wav.mean(0)
    mean, std = float(ref.mean()), float(ref.std()) or 1.0
    x = torch.from_numpy((wav - mean) / std)

    say("STAGE", f"Separating the singing from the music on {device.upper()} — this takes a few minutes…")
    with torch.no_grad():
        out = apply_model(model, x[None], device=device, shifts=1, split=True, overlap=0.25, progress=True)[0]
    out = out.cpu().numpy() * std + mean          # (sources, 2, n)
    names = list(model.sources)
    vi = names.index("vocals")
    vocals = out[vi]
    inst = sum(out[i] for i in range(len(names)) if i != vi)

    say("STAGE", "Saving the instrumental…")
    encode(a.ffmpeg, inst, sr, a.inst)
    if a.vocals:
        encode(a.ffmpeg, vocals, sr, a.vocals)
    say("DONE")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:
        say("ERROR", f"{type(e).__name__}: {e}")
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)   # skip interpreter shutdown (PyTorch can crash there on some Windows setups)
