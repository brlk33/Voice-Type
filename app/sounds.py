"""Soft start/stop chimes (synthesized, no sound files).

start: two quick rising tones; stop: the same, falling; pause: one muted tone.
Quiet on purpose. Played with winsound from memory (Windows only).
"""
import io
import threading
import wave

import numpy as np

SR = 44100
_cache = {}


def _tone(freq, dur, start, total, amp):
    t = np.arange(int(SR * total)) / SR
    x = np.zeros_like(t)
    m = (t >= start) & (t < start + dur + 0.25)
    tt = t[m] - start
    env = np.minimum(1.0, tt / 0.006) * np.exp(-tt / (dur * 0.9))     # 6 ms attack, soft decay
    x[m] = amp * env * (np.sin(2 * np.pi * freq * tt) + 0.18 * np.sin(4 * np.pi * freq * tt))
    return x


def _wav(kind):
    if kind in _cache:
        return _cache[kind]
    total = 0.42
    if kind == "start":
        x = _tone(660, 0.09, 0.0, total, 0.16) + _tone(990, 0.12, 0.075, total, 0.14)
    elif kind == "stop":
        x = _tone(990, 0.09, 0.0, total, 0.14) + _tone(660, 0.13, 0.075, total, 0.15)
    else:  # pause / resume
        x = _tone(784, 0.08, 0.0, 0.3, 0.10)
    fade = np.linspace(1, 0, int(SR * 0.04))
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
    """kind: start | stop | pause. wait=True blocks until it has played (~0.4 s)."""
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
