"""Audio input: microphone, system sound (everything the computer plays), or both mixed.

System sound is captured with WASAPI loopback from the current default output device,
so it works with Zoom, Teams, browsers, players... If the default output changes while
recording (headphones plugged in), capture follows it.
"""
import collections
import logging
import threading
import time

import numpy as np

import core

log = logging.getLogger("voicetype")
SOURCES = ("mic", "system", "both")


class Capture:
    """Calls callback(x) with mono float32 blocks of `block` samples at core.SR, from a
    background thread. start() raises if the source can't be opened."""

    def __init__(self, source, callback, block=800, mic_device=None):
        self.source = source if source in SOURCES else "mic"
        self.callback = callback
        self.block = block
        self.mic_device = mic_device
        self._mic = None
        self._sys_thread = None
        self._running = False
        self._lock = threading.Lock()
        self._bufs = {"mic": collections.deque(), "system": collections.deque()}
        self._lens = {"mic": 0, "system": 0}
        self._out = np.zeros(0, np.float32)
        self._sys_error = None

    # ------------------------------------------------------------ public
    def start(self):
        self._running = True
        if self.source in ("mic", "both"):
            import sounddevice as sd
            self._mic = sd.InputStream(samplerate=core.SR, channels=1, dtype="float32",
                                       blocksize=self.block, device=self.mic_device,
                                       callback=lambda d, f, t, s: self._push("mic", d[:, 0].copy()))
            self._mic.start()
        if self.source in ("system", "both"):
            ready = threading.Event()
            self._sys_thread = threading.Thread(target=self._system_loop, args=(ready,), daemon=True)
            self._sys_thread.start()
            ready.wait(5)
            if self._sys_error:
                self.stop()
                raise RuntimeError(self._sys_error)

    def stop(self):
        self._running = False
        if self._mic is not None:
            try:
                self._mic.stop()
                self._mic.close()
            except Exception:
                pass
            self._mic = None
        if self._sys_thread is not None:
            self._sys_thread.join(2)
            self._sys_thread = None

    # sounddevice-like aliases so callers can treat it as a stream
    def close(self):
        self.stop()

    # ------------------------------------------------------------ system sound
    def _system_loop(self, ready):
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0)  # COM for this thread (MTA)
        except Exception:
            pass
        try:
            import warnings
            import soundcard as sc
            warnings.filterwarnings("ignore", module="soundcard")
        except Exception as e:
            self._sys_error = f"soundcard: {e}"
            ready.set()
            return
        first = True
        while self._running:
            try:
                spk = sc.default_speaker()
                dev = sc.get_microphone(id=str(spk.id), include_loopback=True)
                log.info("Звук системы: %s", spk.name)
                with dev.recorder(samplerate=core.SR, blocksize=core.SR // 10) as rec:
                    if first:
                        ready.set()
                        first = False
                    checked = time.time()
                    while self._running:
                        d = rec.record(numframes=self.block)
                        x = d.mean(axis=1) if d.ndim == 2 else d
                        self._push("system", x.astype(np.float32))
                        if time.time() - checked > 2:
                            checked = time.time()
                            if sc.default_speaker().id != spk.id:
                                log.info("Звук системы: сменилось устройство вывода")
                                break
            except Exception as e:
                log.exception("system audio")
                if first:
                    self._sys_error = str(e)
                    ready.set()
                    return
                time.sleep(1)  # device vanished: retry with the new default

    # ------------------------------------------------------------ mixing
    def _push(self, src, x):
        if not self._running:
            return
        with self._lock:
            if self.source != "both":
                out = x
            else:
                self._bufs[src].append(x)
                self._lens[src] += len(x)
                n = min(self._lens["mic"], self._lens["system"])
                other = "system" if src == "mic" else "mic"
                # one source stalled: don't hold the other back for more than 1 s
                if n == 0 and self._lens[src] > core.SR:
                    self._bufs[other].append(np.zeros(self._lens[src] - self._lens[other], np.float32))
                    self._lens[other] = self._lens[src]
                    n = self._lens[src]
                # clocks of the two devices drift apart slowly: drop the excess of the faster one
                if self._lens[src] - self._lens[other] > 2 * core.SR:
                    self._take(src, self._lens[src] - self._lens[other] - core.SR // 2)
                    n = min(self._lens["mic"], self._lens["system"])
                if n == 0:
                    return
                out = self._take("mic", n) + self._take("system", n)
                np.clip(out, -1.0, 1.0, out=out)
            self._out = np.concatenate([self._out, out]) if len(self._out) else out
            blocks = []
            while len(self._out) >= self.block:
                blocks.append(self._out[:self.block])
                self._out = self._out[self.block:]
        for b in blocks:
            try:
                self.callback(b)
            except Exception:
                log.exception("audio callback")

    def _take(self, src, n):
        q, parts, got = self._bufs[src], [], 0
        while got < n:
            a = q.popleft()
            if got + len(a) > n:
                q.appendleft(a[n - got:])
                a = a[:n - got]
            parts.append(a)
            got += len(a)
        self._lens[src] -= n
        return np.concatenate(parts)
