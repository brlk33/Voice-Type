"""Soft start/stop clicks (synthesized, no sound files).

A short muted «tick»: higher for start, lower for stop, quieter for pause.
Played with winsound from memory (Windows only).
"""
import io
import threading
import wave

import numpy as np

SR = 44100
_cache = {}


def _click(freq, start, total, amp, decay=0.011):
    """A short soft «tick»: a damped tone plus a tiny filtered noise transient."""
    n = int(SR * total)
    t = np.arange(n) / SR
    x = np.zeros(n)
    m = t >= start
    tt = t[m] - start
    body = np.sin(2 * np.pi * freq * tt) * np.exp(-tt / decay)
    body *= np.minimum(1.0, tt / 0.0015)                       # 1.5 ms attack: no hard pop
    rng = np.random.default_rng(int(freq))
    noise = rng.standard_normal(len(tt)) * np.exp(-tt / 0.0025)
    noise = np.convolve(noise, np.ones(12) / 12, mode="same")  # take the hiss off
    x[m] = amp * (0.85 * body + 0.35 * noise)
    return x


def _wav(kind):
    if kind in _cache:
        return _cache[kind]
    total = 0.12
    if kind == "start":
        x = _click(1250, 0.0, total, 0.22)
    elif kind == "stop":
        x = _click(820, 0.0, total, 0.22, decay=0.013)
    else:  # pause / resume
        x = _click(1000, 0.0, total, 0.13, decay=0.008)
    fade = np.linspace(1, 0, int(SR * 0.02))
    x[-len(fade):] *= fade
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    _cache[kind] = buf.getvalue()
    return _cache[kind]


def play(kind, wait=False):
    """kind: start | stop | pause. wait=True blocks until it has played (~0.12 s)."""
    try:
        import winsound
    except ImportError:
        return
    data = _wav(kind)

    def run():
        try:
            winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
        except Exception:
            pass
    if wait:
        run()
    else:
        threading.Thread(target=run, daemon=True).start()
