"""VoiceType: window + tray + global dictation hotkey."""
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402
import editor  # noqa: E402

core.setup_logging("app")
log = core.log

import numpy as np  # noqa: E402

IS_WIN = os.name == "nt"
PORT = 47653
HISTORY = core.ROOT / "history.txt"
TRANSCRIPTS = core.ROOT / "transcripts"
STARTUP_LNK = os.path.join(os.environ.get("APPDATA", ""),
                           r"Microsoft\Windows\Start Menu\Programs\Startup\VoiceType.lnk")
AUDIO_TYPES = ("*.mp3;*.wav;*.m4a;*.ogg;*.opus;*.oga;*.flac;*.aac;*.wma;*.amr;*.aiff;*.aif;"
               "*.mp4;*.mkv;*.mov;*.avi;*.webm;*.m4v;*.wmv;*.flv;*.ts;*.mts;*.m2ts;*.mpg;*.mpeg;*.3gp;*.ogv")

STR = {
    "ru": {"open": "Открыть VoiceType", "quit": "Выход", "busy": "Распознаю",
           "media": "Аудио и видео", "all": "Все файлы",
           "loading": "Загрузка модели…", "ready": "Готов", "rec": "Запись…",
           "proc": "Распознаю…", "error": "Ошибка"},
    "en": {"open": "Open VoiceType", "quit": "Quit", "busy": "Transcribing",
           "media": "Audio and video", "all": "All files",
           "loading": "Loading model…", "ready": "Ready", "rec": "Recording…",
           "proc": "Transcribing…", "error": "Error"},
}


def message_box(text: str, title: str = "VoiceType"):
    if IS_WIN:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)


def beep(freq: int):
    if IS_WIN:
        import winsound
        threading.Thread(target=winsound.Beep, args=(freq, 70), daemon=True).start()


def make_icon(color):
    return core.draw_logo(64, bg=color)


COLORS = {"loading": (128, 128, 128), "ready": (55, 53, 47), "rec": (224, 62, 62),
          "busy": (217, 115, 13), "error": (120, 0, 0)}


def run_ps(script: str) -> bool:
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, creationflags=core.NO_WINDOW)
    return r.returncode == 0


def system_dark() -> bool:
    if not IS_WIN:
        return False
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


# ======================================================================= engine
class Engine:
    """Model, hotkey, microphone, paste. UI is notified through callbacks."""

    def __init__(self, on_state, on_history):
        self.cfg = core.load_config()
        self.dic = core.Dictionary()
        self.rec = None
        self.on_state = on_state
        self.on_history = on_history
        self.state, self.detail = "loading", ""
        self.recording = False
        self.frames = []
        self.level = 0.0
        self.stream = None
        self.hot_down = False
        self.block_dictation = False
        from pynput import keyboard
        self.kb = keyboard
        self.ctl = keyboard.Controller()
        self.hotkey = self.parse_key(self.cfg["hotkey"])
        from overlay import Overlay
        self.overlay = Overlay(lambda: self.level)
        self.overlay.enabled = self.cfg.get("overlay", True)
        self.overlay.busy_text = self.s("busy")
        self._actions = queue.Queue()
        threading.Thread(target=self._act, daemon=True).start()

    def s(self, key):
        return STR.get(self.cfg.get("ui_lang", "ru"), STR["ru"])[key]

    def editor_on(self, mode):
        """mode: dictation | recording | files | youtube"""
        return bool(self.cfg.get("editor")) and bool(self.cfg.get("editor_" + mode, True)) and editor.installed()

    def editor_terms(self):
        terms = [t.strip() for t in self.cfg.get("editor_terms", []) if t.strip()]
        return list(dict.fromkeys(terms + sorted(self.dic.targets())))

    def set_state(self, state, detail=""):
        self.state, self.detail = state, detail
        self.on_state(state, detail)

    def start(self):
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        try:
            self.rec = core.Recognizer(self.cfg)
            self.kb.Listener(on_press=self.on_press, on_release=self.on_release).start()
            self.set_state("ready")
        except Exception as e:
            log.exception("Ошибка загрузки")
            self.set_state("error", str(e))
            message_box(f"Не удалось загрузить распознавание:\n{e}\n\nПодробности в logs\\app.log")

    def reload_model(self, name):
        def work():
            self.set_state("loading")
            try:
                new = core.Recognizer(dict(self.cfg, model=name))
                old, self.rec = self.rec, new
                del old
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                self.set_state("ready")
            except Exception as e:
                log.exception("Смена модели")
                self.set_state("ready" if self.rec else "error", str(e))
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ hotkey
    def parse_key(self, name: str):
        name = name.strip().lower()
        if hasattr(self.kb.Key, name):
            return getattr(self.kb.Key, name)
        if len(name) == 1:
            return self.kb.KeyCode.from_char(name)
        raise ValueError(f"Unknown key: {name}")

    def on_press(self, key):
        # runs inside the Windows keyboard hook: must return fast, real work goes to _act
        if key == self.hotkey:
            if self.hot_down:  # autorepeat
                return
            self.hot_down = True
            self._actions.put("down")
        elif self.hot_down:
            self._actions.put("other")

    def on_release(self, key):
        if key == self.hotkey:
            self.hot_down = False
            self._actions.put("up")

    def _act(self):
        while True:
            a = self._actions.get()
            try:
                hold = self.cfg["mode"] == "hold"
                if a == "down":
                    if hold or not self.recording:
                        self.start_recording()
                    else:
                        self.stop_recording()
                elif a == "up" and hold and self.recording:
                    self.stop_recording()
                elif a == "other" and hold and self.recording:
                    self.cancel_recording()  # hotkey used as a modifier, e.g. Ctrl+C
            except Exception:
                log.exception("hotkey action")

    # ------------------------------------------------------------ audio
    def start_recording(self):
        if self.rec is None or self.recording or getattr(self, "block_dictation", False):
            return
        import sounddevice as sd
        self.frames = []

        def cb(indata, frames, t, status):
            x = indata[:, 0].copy()
            self.frames.append(x)
            self.level = self.level * 0.5 + float(np.sqrt(np.mean(x * x))) * 0.5
        try:
            self.stream = sd.InputStream(samplerate=core.SR, channels=1, dtype="float32",
                                         device=self.cfg.get("mic_device"), callback=cb)
            self.stream.start()
        except Exception as e:
            log.exception("Микрофон")
            self.set_state("error", f"Mic: {e}")
            return
        self.recording = True
        if self.cfg["sounds"]:
            beep(880)
        self.overlay.set("rec")
        self.set_state("rec")

    def _close_stream(self):
        self.recording = False
        self.level = 0.0
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        audio = np.concatenate(self.frames) if self.frames else np.zeros(0, np.float32)
        self.frames = []
        return audio

    def cancel_recording(self):
        self._close_stream()
        self.overlay.set("idle")
        self.set_state("ready")

    def stop_recording(self):
        audio = self._close_stream()
        if self.cfg["sounds"]:
            beep(600)
        if len(audio) < core.SR * 0.3:
            self.overlay.set("idle")
            self.set_state("ready")
            return
        self.overlay.set("busy")
        self.set_state("busy")
        threading.Thread(target=self.process, args=(audio,), daemon=True).start()

    def process(self, audio):
        try:
            t = time.time()
            if self.cfg.get("paragraphs", True):
                paras = self.rec.dictation_paragraphs(audio, float(self.cfg.get("paragraph_pause", 1.2)))
            else:
                one = self.rec.transcribe(audio)
                paras = [one] if one else []
            protect = self.dic.targets()
            style = self.cfg.get("style", "formal")
            clean = self.cfg.get("clean", True)
            fill = self.cfg.get("clean_fillers", False)
            paras = [self.dic.apply(p).strip() for p in paras]
            if clean:
                paras = [core.clean_text(p, fill) for p in paras]
            if self.editor_on("dictation"):
                t1 = time.time()
                paras = [editor.edit(p, self.editor_terms()) for p in paras]
                log.info("Редактор (диктовка): %.2f с", time.time() - t1)
            paras = [core.apply_style(p, style, protect) for p in paras]
            text = "\n\n".join(p for p in paras if p)
            log.info("Диктовка %.1f с -> %.2f с: %s", len(audio) / core.SR, time.time() - t, text)
            if text:
                self.overlay.set("idle")
                now = datetime.now()
                one_line = text.replace("\n", "\\n")
                with open(HISTORY, "a", encoding="utf-8") as f:
                    f.write(f"[{now:%Y-%m-%d %H:%M:%S}] {one_line}\n")
                self.on_history({"time": now.strftime("%d.%m %H:%M"), "text": text})
                self.paste(text + (" " if self.cfg["add_trailing_space"] else ""))
        except Exception as e:
            log.exception("Ошибка распознавания")
            self.set_state("error", str(e))
            time.sleep(2)
        finally:
            if not self.recording:
                self.overlay.set("idle")
                self.set_state("ready")

    def paste(self, text):
        import pyperclip
        old = None
        try:
            old = pyperclip.paste()
        except Exception:
            pass
        pyperclip.copy(text)
        time.sleep(0.05)
        Key, KeyCode = self.kb.Key, self.kb.KeyCode
        v = KeyCode.from_vk(0x56)  # physical V: works in any keyboard layout
        self.ctl.press(Key.ctrl)
        self.ctl.press(v)
        self.ctl.release(v)
        self.ctl.release(Key.ctrl)
        if old is not None:
            time.sleep(0.4)
            try:
                pyperclip.copy(old)
            except Exception:
                pass


# ======================================================================= transcripts store
def _tr_path(tid):
    return TRANSCRIPTS / f"{tid}.json"


def tr_load(tid):
    with open(_tr_path(tid), encoding="utf-8") as f:
        return json.load(f)


def tr_save(tr):
    TRANSCRIPTS.mkdir(exist_ok=True)
    tmp = _tr_path(tr["id"]).with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tr, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _tr_path(tr["id"]))


def tr_text(tr, timestamps=False):
    out = []
    for s in tr["segments"]:
        if timestamps:
            out.append(f"[{core.fmt_time(int(s['t'] * core.SR))}] {s['text']}")
        else:
            out.append(s["text"])
    return "\n\n".join(out)



# ======================================================================= frameless window control (Win32)
class WinCtl:
    """Drag, resize and maximize a frameless window natively (smooth, works across monitors)."""

    def __init__(self):
        self.hwnd = None
        self.maxed = False
        self.normal = None
        if IS_WIN:
            import ctypes
            from ctypes import wintypes
            self.ct, self.wt = ctypes, wintypes
            self.u32 = ctypes.windll.user32
            # 64-bit safe signature (HWND_TOPMOST = -1 must be passed as a pointer)
            self.u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                              ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            self.u32.SetWindowPos.restype = wintypes.BOOL

    def attach(self, hwnd):
        self.hwnd = hwnd
        self.corners(True)

    def corners(self, round_):
        try:  # Windows 11: DWMWA_WINDOW_CORNER_PREFERENCE = 33; ROUND = 2, DONOTROUND = 1
            v = self.ct.c_int(2 if round_ else 1)
            self.ct.windll.dwmapi.DwmSetWindowAttribute(self.hwnd, 33, self.ct.byref(v), 4)
        except Exception:
            pass

    def _cursor(self):
        pt = self.wt.POINT()
        self.u32.GetCursorPos(self.ct.byref(pt))
        return pt.x, pt.y

    def _rect(self):
        r = self.wt.RECT()
        self.u32.GetWindowRect(self.hwnd, self.ct.byref(r))
        return r.left, r.top, r.right, r.bottom

    def _down(self):
        vk = 0x02 if self.u32.GetSystemMetrics(23) else 0x01  # swapped mouse buttons
        return self.u32.GetAsyncKeyState(vk) & 0x8000

    def _set(self, x, y, w, h):
        self.u32.SetWindowPos(self.hwnd, 0, int(x), int(y), int(w), int(h), 0x0004 | 0x0010)

    def _scale(self):
        try:
            return self.u32.GetDpiForWindow(self.hwnd) / 96.0
        except Exception:
            return 1.0

    def _work_area(self):
        class MI(self.ct.Structure):
            _fields_ = [("cbSize", self.wt.DWORD), ("rcMonitor", self.wt.RECT),
                        ("rcWork", self.wt.RECT), ("dwFlags", self.wt.DWORD)]
        mi = MI()
        mi.cbSize = self.ct.sizeof(MI)
        mon = self.u32.MonitorFromWindow(self.hwnd, 2)
        self.u32.GetMonitorInfoW(mon, self.ct.byref(mi))
        r = mi.rcWork
        return r.left, r.top, r.right, r.bottom

    def drag(self):
        if not self.hwnd:
            return
        cx, cy = self._cursor()
        l, t, r, b = self._rect()
        if self.maxed:
            # pull the window out of maximized state under the cursor
            nl, nt, nr, nb = self.normal
            w, h = nr - nl, nb - nt
            frac = (cx - l) / max(1, r - l)
            l, t = cx - int(w * frac), t
            moved = False
            while self._down():
                x, y = self._cursor()
                if abs(x - cx) + abs(y - cy) > 6:
                    moved = True
                    break
                time.sleep(0.01)
            if not moved:
                return
            self.maxed = False
            self.corners(True)
            self._set(l, t, w, h)
            r, b = l + w, t + h
        ox, oy = cx - l, cy - t
        while self._down():
            x, y = self._cursor()
            self.u32.SetWindowPos(self.hwnd, 0, x - ox, y - oy, 0, 0, 0x0001 | 0x0004 | 0x0010)
            time.sleep(0.006)

    def resize(self, edge):
        if not self.hwnd or self.maxed:
            return
        sx, sy = self._cursor()
        l, t, r, b = self._rect()
        sc = self._scale()
        mw, mh = int(760 * sc), int(520 * sc)
        while self._down():
            x, y = self._cursor()
            dx, dy = x - sx, y - sy
            L, T, R, B = l, t, r, b
            if "e" in edge:
                R = max(r + dx, l + mw)
            if "s" in edge:
                B = max(b + dy, t + mh)
            if "w" in edge:
                L = min(l + dx, r - mw)
            if "n" in edge:
                T = min(t + dy, b - mh)
            self._set(L, T, R - L, B - T)
            time.sleep(0.012)

    def toggle_max(self):
        if not self.hwnd:
            return False
        if self.maxed:
            l, t, r, b = self.normal
            self.maxed = False
            self.corners(True)
            self._set(l, t, r - l, b - t)
        else:
            self.normal = self._rect()
            l, t, r, b = self._work_area()
            self.maxed = True
            self.corners(False)
            self._set(l, t, r - l, b - t)
        return self.maxed


# ======================================================================= long recording (lessons, lectures)
class LongRecorder:
    """Records for as long as needed. Audio lives only in memory and is thrown away as soon as
    a piece is recognized; only the text is saved (after every piece, so nothing is lost)."""

    WINDOW = 20 * core.SR      # max piece sent to the model
    START = 18 * core.SR       # start a piece once this much new audio is waiting
    BLOCK = 800                # 50 ms per waveform bar

    def __init__(self, api):
        self.api = api
        self.state = "idle"    # idle | rec | paused | finishing
        self.open_after = False
        self.lock = threading.Lock()
        self._reset()

    def _reset(self):
        self.buf = np.zeros(core.SR * 120, np.int16)  # only audio not yet recognized
        self.base = 0          # absolute sample index of buf[0]
        self.total = 0         # absolute samples recorded
        self.peaks = []
        self._peak_shift = 0
        self.done_upto = 0     # absolute samples already recognized
        self.tr = None
        self.stream = None
        self.error = ""

    # ---------------------------------------------------------------- control
    def start(self):
        eng = self.api._engine
        if self.state != "idle":
            return {"ok": False}
        if eng.rec is None:
            return {"ok": False, "error": "model"}
        if eng.recording:
            eng.cancel_recording()
        self._reset()
        now = datetime.now()
        word = "Запись" if eng.cfg.get("ui_lang", "ru") == "ru" else "Recording"
        self.tr = {"id": uuid.uuid4().hex[:12], "title": f"{word} {now:%d.%m.%Y %H:%M}",
                   "source": "", "kind": "mic", "created": now.isoformat(timespec="seconds"),
                   "duration": 0, "model": eng.rec.model_name, "segments": [], "recording": True}
        tr_save(self.tr)
        import sounddevice as sd
        try:
            self.stream = sd.InputStream(samplerate=core.SR, channels=1, dtype="int16",
                                         blocksize=self.BLOCK, device=eng.cfg.get("mic_device"),
                                         callback=self._cb)
            self.stream.start()
        except Exception as e:
            log.exception("long rec mic")
            self._discard()
            self._reset()
            return {"ok": False, "error": str(e)}
        eng.block_dictation = True
        self.state = "rec"
        threading.Thread(target=self._worker, daemon=True).start()
        self.api._js("vt.onTranscripts()")
        log.info("Долгая запись: старт (без аудиофайла)")
        return {"ok": True, "tid": self.tr["id"]}

    def pause(self):
        if self.state == "rec":
            self.state = "paused"

    def resume(self):
        if self.state == "paused":
            self.state = "rec"

    def stop(self):
        if self.state not in ("rec", "paused"):
            return
        self.state = "finishing"
        self._close_input()
        log.info("Долгая запись: стоп, %.1f с", self.total / core.SR)

    def cancel(self):
        if self.state not in ("rec", "paused"):
            return
        self.state = "idle"
        self._close_input()
        time.sleep(0.2)
        self._discard()
        self._reset()
        self.api._engine.block_dictation = False
        self.api._js("vt.onTranscripts()")

    def emergency_close(self):
        """App is quitting: keep the text recognized so far."""
        if self.state == "idle":
            return
        try:
            self._close_input()
            if self.tr:
                self.tr.update(duration=self.total / core.SR, recording=False)
                tr_save(self.tr)
        except Exception:
            log.exception("emergency close")

    def status(self, since_peak=0, since_seg=0):
        segs = self.tr["segments"] if self.tr else []
        return {"state": self.state, "elapsed": self.total / core.SR,
                "peaks": self.peaks[max(0, since_peak - getattr(self, "_peak_shift", 0)):],
                "npeaks": len(self.peaks) + getattr(self, "_peak_shift", 0),
                "segs": segs[max(0, since_seg - 1):], "segfrom": max(0, since_seg - 1), "nsegs": len(segs),
                "pending": max(0, self.total - self.done_upto) / core.SR,
                "tid": self.tr["id"] if self.tr else None, "error": self.error}

    # ---------------------------------------------------------------- internals
    def _cb(self, indata, frames, t, status):
        if self.state != "rec":
            return
        x = indata[:, 0]
        with self.lock:
            # drop audio that is already recognized, then append
            drop = self.done_upto - self.base
            if drop > core.SR * 30:
                keep = self.total - self.done_upto
                self.buf[:keep] = self.buf[drop:drop + keep]
                self.base = self.done_upto
            end = self.total - self.base + len(x)
            if end > len(self.buf):
                bigger = np.zeros(max(end, len(self.buf) * 2), np.int16)
                bigger[:self.total - self.base] = self.buf[:self.total - self.base]
                self.buf = bigger
            self.buf[self.total - self.base:end] = x
            self.total += len(x)
            self.peaks.append(round(float(np.abs(x.astype(np.int32)).max()) / 32768, 4))
            if len(self.peaks) > 6000:
                del self.peaks[:1000]
                self._peak_shift = getattr(self, "_peak_shift", 0) + 1000

    def _close_input(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass

    def _discard(self):
        try:
            if self.tr and os.path.exists(_tr_path(self.tr["id"])):
                os.remove(_tr_path(self.tr["id"]))
        except Exception:
            log.exception("discard")

    def _audio(self, a, b):
        with self.lock:
            b = min(b, self.total)
            return self.buf[a - self.base:b - self.base].astype(np.float32) / 32768.0

    def _add(self, start, end, text):
        """Add a recognized piece: glue it to the last paragraph or start a new one."""
        eng = self.api._engine
        clean = eng.cfg.get("clean", True)
        fill = eng.cfg.get("clean_fillers", False)
        text = eng.dic.apply(text).strip()
        if clean:
            text = core.clean_text(text, fill)
        if text and eng.editor_on("recording"):
            text = editor.edit(text, eng.editor_terms())
        segs = self.tr["segments"]
        if text:
            st, en = start / core.SR, end / core.SR
            mode = eng.cfg.get("long_paragraphs", "long")
            if segs and not core.new_paragraph(mode, st - segs[-1].get("end", st), len(segs[-1]["text"])):
                last = segs[-1]
                joined = core.join_piece(last["text"], text, st - last.get("end", st))
                last["text"] = core.clean_text(joined, fill) if clean else joined
                last["end"] = en
            else:
                segs.append({"t": st, "end": en, "text": text})
        self.tr["duration"] = self.total / core.SR
        tr_save(self.tr)

    def _next_piece(self):
        """Recognize one piece ending at a pause."""
        rec = self.api._engine.rec
        a = self.done_upto
        piece = self._audio(a, a + self.WINDOW)
        ts = rec.vad(piece, min_silence_duration_ms=300, speech_pad_ms=120)
        if not ts:
            self.done_upto = a + len(piece)
            return
        cut = None
        best = 0
        for s1, s2 in zip(ts, ts[1:]):
            gap = s2["start"] - s1["end"]
            if s1["end"] >= 6 * core.SR and gap >= best:
                best, cut = gap, (s1["end"] + s2["start"]) // 2
        if cut is None:
            last_end = ts[-1]["end"]
            cut = last_end if len(piece) - last_end > core.SR // 2 else len(piece)
        cut = max(cut, core.SR)
        t0 = time.time()
        text = rec._recognize_short(piece[:cut])
        log.info("Запись: кусок %.1f–%.1f с за %.2f с", a / core.SR, (a + cut) / core.SR, time.time() - t0)
        inside = [t for t in ts if t["start"] < cut]
        sp_start = inside[0]["start"] if inside else 0
        sp_end = min(cut, max(t["end"] for t in inside)) if inside else cut
        self._add(a + sp_start, a + sp_end, text)
        self.done_upto = a + cut

    def _worker(self):
        try:
            while self.state in ("rec", "paused"):
                if self.total - self.done_upto >= self.START:
                    self._next_piece()
                else:
                    time.sleep(0.5)
            if self.state != "finishing":
                return
            while self.done_upto < self.total:
                if self.total - self.done_upto > self.WINDOW:
                    self._next_piece()
                else:
                    rest = self._audio(self.done_upto, self.total)
                    for seg in self.api._engine.rec.transcribe_segments(rest):
                        self._add(self.done_upto + int(seg["t"] * core.SR),
                                  self.done_upto + int(seg.get("end", seg["t"]) * core.SR), seg["text"])
                    self.done_upto = self.total
            self.tr.update(duration=self.total / core.SR, recording=False)
            tr_save(self.tr)
            log.info("Долгая запись готова: %d абзацев", len(self.tr["segments"]))
            self.api._js(f"vt.onRecDone({json.dumps(self.tr['id'])})")
            if self.open_after:
                self.open_after = False
                threading.Thread(target=self.api._show_main, daemon=True).start()
                self.api._js(f"go({json.dumps('tr:' + self.tr['id'])})")
        except Exception as e:
            log.exception("long rec worker")
            self.error = str(e)
            if self.tr:
                self.tr["recording"] = False
                tr_save(self.tr)
            self.api._js(f"vt.onRecDone({json.dumps(self.tr['id'] if self.tr else None)})")
        finally:
            if self.state == "finishing" or self.error:
                self.state = "idle"
                self.buf = np.zeros(0, np.int16)  # free the memory
                self.api._engine.block_dictation = False
                self.api._js("vt.onTranscripts()")


# ======================================================================= JS API
class Api:
    """Methods callable from the page as pywebview.api.<name>()."""

    def __init__(self):
        self._window = None
        self._show_main = lambda: None
        self._win = WinCtl()
        self._jobs = {}
        self._queue = queue.Queue()
        self._next = 1
        self._out = queue.Queue()
        threading.Thread(target=self._pusher, daemon=True).start()
        self._engine = Engine(self._push_state, self._push_history)
        self._long = LongRecorder(self)
        self._yt_jobs = {}
        self._yt_queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._yt_worker, daemon=True).start()
        threading.Thread(target=self._auto_update, daemon=True).start()

    # ------------- push to the page (never from the keyboard hook thread directly)
    def _js(self, code):
        self._out.put(code)

    def _pusher(self):
        while True:
            item = self._out.get()
            try:
                if callable(item):
                    item()
                elif self._window is not None:
                    self._window.evaluate_js(item)
            except Exception:
                pass

    def _push_state(self, state, detail):
        self._js(f"vt.onState({json.dumps({'state': state, 'detail': detail}, ensure_ascii=False)})")
        self._out.put(lambda: tray_update(state, self._engine))

    def _push_history(self, item):
        self._js(f"vt.onHistory({json.dumps(item, ensure_ascii=False)})")

    def _push_job(self, job):
        self._js(f"vt.onJob({json.dumps(job, ensure_ascii=False)})")

    # ------------- long recording
    def rec_start(self):
        return self._long.start()

    def rec_pause(self):
        self._long.pause()

    def rec_resume(self):
        self._long.resume()

    def rec_stop(self):
        self._long.stop()

    def rec_cancel(self):
        self._long.cancel()

    def rec_status(self, since_peak=0, since_seg=0):
        return self._long.status(int(since_peak), int(since_seg))

    # ------------- updates
    def get_version(self):
        import updater
        return {"version": updater.local_version(), "repo": updater.REPO,
                "pending": getattr(self, "_update_pending", None)}

    def check_updates(self):
        import updater
        res = updater.check(apply=True)
        if res.get("updated"):
            self._update_pending = res["latest"]
        return res

    def _auto_update(self):
        time.sleep(8)  # let the model load first
        if not self._engine.cfg.get("auto_update", True):
            return
        res = self.check_updates()
        if res.get("updated"):
            self._js(f"vt.onUpdate({json.dumps(res, ensure_ascii=False)})")

    def restart(self):
        """Start a fresh copy of the app and quit this one (to load an installed update)."""
        if self._long.state != "idle":
            return {"ok": False, "error": "recording"}
        try:
            if _srv is not None:
                _srv.close()  # free the single-instance port for the new process
        except Exception:
            pass
        exe = sys.executable
        if exe.lower().endswith("python.exe") and os.path.exists(exe[:-10] + "pythonw.exe"):
            exe = exe[:-10] + "pythonw.exe"
        subprocess.Popen([exe, str(core.ROOT / "app" / "app.py")], cwd=str(core.ROOT),
                         creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        log.info("Перезапуск для обновления")
        threading.Timer(0.3, lambda: os._exit(0)).start()
        return {"ok": True}

    # ------------- editor model
    def editor_status(self):
        st = editor.install_status()
        st["gpu"] = editor.SERVER.gpu
        return st

    def editor_install(self):
        def done():
            if editor.installed():
                self._engine.cfg["editor"] = True
                self.save_settings({"editor": True})
            self._js("vt.onEditor && vt.onEditor()")
        editor.install_async(done)
        return True

    def editor_remove(self):
        editor.uninstall()
        self.save_settings({"editor": False})
        return True

    def editor_test(self, text):
        eng = self._engine
        raw = eng.dic.apply(text)
        if eng.cfg.get("clean", True):
            raw = core.clean_text(raw, eng.cfg.get("clean_fillers", False))
        t = time.time()
        out = editor.edit(raw, eng.editor_terms())
        return {"text": out, "sec": round(time.time() - t, 2)}

    # ------------- window chrome
    def win_drag(self):
        self._win.drag()

    def win_resize(self, edge):
        self._win.resize(edge)

    def win_toggle_max(self):
        return self._win.toggle_max()

    def win_min(self):
        self._window.minimize()

    def win_close(self):
        self._window.hide()

    def get_state(self):
        return {"state": self._engine.state, "detail": self._engine.detail}

    # ------------- files in
    def pick_files(self):
        import webview
        e = self._engine
        paths = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=(f"{e.s('media')} ({AUDIO_TYPES})", f"{e.s('all')} (*.*)"))
        if paths:
            self.add_files(list(paths))

    def take_dropped(self):
        """Called by the page right after a drop; WebView2 has delivered the real paths by then."""
        from webview.dom import _dnd_state
        for _ in range(20):
            if _dnd_state["paths"]:
                break
            time.sleep(0.05)
        paths = [p for _, p in _dnd_state["paths"]]
        _dnd_state["paths"].clear()
        log.info("Перетащено файлов: %d %s", len(paths), paths)
        self.add_files(paths)
        return len(paths)

    def add_files(self, paths):
        for p in paths:
            if not os.path.isfile(p):
                log.info("Пропуск (не файл): %s", p)
                continue
            job = {"id": self._next, "path": p, "name": os.path.basename(p), "status": "wait",
                   "progress": None, "error": None, "tid": None}
            self._next += 1
            self._jobs[job["id"]] = job
            self._push_job(job)
            self._queue.put(job["id"])

    def get_jobs(self):
        return sorted(self._jobs.values(), key=lambda j: j["id"])

    # ------------- YouTube (subtitles, no download)
    def add_youtube(self, url, pref="orig"):
        import youtube
        url = (url or "").strip()
        if not youtube.video_id(url):
            return {"ok": False, "error": "badurl"}
        job = {"id": self._next, "url": url, "name": url, "status": "wait", "error": None,
               "code": None, "tid": None, "pref": pref}
        self._next += 1
        self._yt_jobs[job["id"]] = job
        self._push_yt(job)
        self._yt_queue.put(job["id"])
        return {"ok": True}

    def get_yt_jobs(self):
        return sorted(self._yt_jobs.values(), key=lambda j: j["id"])

    def _push_yt(self, job):
        self._js(f"vt.onYtJob({json.dumps(job, ensure_ascii=False)})")

    def _yt_worker(self):
        import youtube
        while True:
            job = self._yt_jobs[self._yt_queue.get()]
            job["status"] = "run"
            self._push_yt(job)
            try:
                r = youtube.fetch(job["url"], job["pref"])
                eng = self._engine
                mode = eng.cfg.get("long_paragraphs", "long")
                paras = youtube.paragraphs(r["cues"], mode)
                if eng.cfg.get("clean", True):
                    for p in paras:
                        p["text"] = core.clean_text(p["text"], eng.cfg.get("clean_fillers", False))
                if eng.editor_on("youtube"):
                    job["status"] = "edit"
                    for i, p in enumerate(paras):
                        job["progress"] = i * 100 // max(1, len(paras))
                        self._push_yt(job)
                        p["text"] = editor.edit(p["text"], eng.editor_terms())
                tr = {"id": uuid.uuid4().hex[:12], "title": r["title"], "source": r["url"], "kind": "youtube",
                      "channel": r["channel"], "sub_lang": r["lang"], "sub_kind": r["kind"],
                      "created": datetime.now().isoformat(timespec="seconds"),
                      "duration": r["duration"], "model": "", "segments": [p for p in paras if p["text"]]}
                tr_save(tr)
                job.update(status="done", tid=tr["id"], name=r["title"])
                log.info("YouTube: %s (%s, %s), %d абзацев", r["title"], r["lang"], r["kind"], len(paras))
                self._js("vt.onTranscripts()")
            except youtube.YTError as e:
                log.info("YouTube ошибка %s: %s", e.code, e.detail)
                job.update(status="err", code=e.code, error=e.detail[:300])
            except Exception as e:
                log.exception("YouTube")
                job.update(status="err", code="other", error=str(e)[:300])
            self._push_yt(job)

    def open_url(self, url):
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            os.startfile(url)

    def _worker(self):
        while True:
            job = self._jobs[self._queue.get()]
            while self._engine.rec is None:
                time.sleep(0.5)
            job["status"] = "run"
            self._push_job(job)
            try:
                last = [-1]

                def prog(k, n):
                    pct = k * 100 // n
                    if pct != last[0]:
                        last[0] = pct
                        job["progress"] = pct
                        self._push_job(job)
                eng = self._engine
                audio = core.decode_file(job["path"])
                job["progress"] = 0
                self._push_job(job)
                segs = eng.rec.transcribe_segments(audio, progress=prog)
                for s in segs:
                    s["text"] = eng.dic.apply(s["text"])
                    if eng.cfg.get("clean", True):
                        s["text"] = core.clean_text(s["text"], eng.cfg.get("clean_fillers", False))
                segs = [s for s in segs if s["text"]]
                segs = core.merge_segments(segs, eng.cfg.get("long_paragraphs", "long"))
                if eng.cfg.get("clean", True):
                    for s in segs:
                        s["text"] = core.clean_text(s["text"], eng.cfg.get("clean_fillers", False))
                if eng.editor_on("files"):
                    job["status"] = "edit"
                    for i, sg in enumerate(segs):
                        job["progress"] = i * 100 // max(1, len(segs))
                        self._push_job(job)
                        sg["text"] = editor.edit(sg["text"], eng.editor_terms())
                tr = {"id": uuid.uuid4().hex[:12],
                      "title": os.path.splitext(job["name"])[0],
                      "source": job["path"],
                      "created": datetime.now().isoformat(timespec="seconds"),
                      "duration": len(audio) / core.SR,
                      "model": eng.rec.model_name,
                      "segments": segs}
                tr_save(tr)
                job.update(status="done", progress=100, tid=tr["id"])
                self._js("vt.onTranscripts()")
            except Exception as e:
                log.exception("Файл %s", job["path"])
                job.update(status="err", error=str(e)[:300])
            self._push_job(job)

    # ------------- transcripts
    def list_transcripts(self):
        items = []
        if TRANSCRIPTS.exists():
            for p in TRANSCRIPTS.glob("*.json"):
                try:
                    tr = tr_load(p.stem)
                    item = {k: tr[k] for k in ("id", "title", "created", "duration")}
                    item["kind"] = tr.get("kind", "file")
                    item["recording"] = bool(tr.get("recording")) and self._long.tr is not None \
                        and self._long.tr["id"] == tr["id"] and self._long.state != "idle"
                    items.append(item)
                except Exception:
                    log.exception("transcript %s", p)
        return sorted(items, key=lambda x: x["created"], reverse=True)

    def get_transcript(self, tid):
        return tr_load(tid)

    def rename_transcript(self, tid, title):
        if self._long.tr is not None and self._long.tr["id"] == tid and self._long.state != "idle":
            self._long.tr["title"] = title.strip() or self._long.tr["title"]
            tr_save(self._long.tr)
            return True
        tr = tr_load(tid)
        tr["title"] = title.strip() or tr["title"]
        tr_save(tr)
        return True

    def delete_transcript(self, tid):
        if self._long.tr is not None and self._long.tr["id"] == tid and self._long.state in ("rec", "paused"):
            self._long.cancel()
            return True
        try:
            os.remove(_tr_path(tid))
        except FileNotFoundError:
            pass
        return True

    def copy_transcript(self, tid, timestamps=False):
        import pyperclip
        pyperclip.copy(tr_text(tr_load(tid), timestamps))
        return True

    def export_transcript(self, tid, timestamps=False):
        import webview
        tr = tr_load(tid)
        src_dir = os.path.dirname(tr.get("source") or "") or str(core.ROOT)
        res = self._window.create_file_dialog(
            webview.SAVE_DIALOG, directory=src_dir if os.path.isdir(src_dir) else "",
            save_filename=f"{tr['title']}.txt")
        if not res:
            return False
        path = res if isinstance(res, str) else res[0]
        with open(path, "w", encoding="utf-8") as f:
            f.write(tr_text(tr, timestamps) + "\n")
        return True

    def reveal(self, path):
        if os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    def copy_text(self, text):
        import pyperclip
        pyperclip.copy(text)
        return True

    # ------------- history
    def get_history(self):
        items = []
        if HISTORY.exists():
            for line in HISTORY.read_text(encoding="utf-8").splitlines():
                if line.startswith("[") and "] " in line:
                    ts, text = line[1:].split("] ", 1)
                    try:
                        ts = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime("%d.%m %H:%M")
                    except ValueError:
                        pass
                    items.append({"time": ts, "text": text.replace("\\n", "\n")})
        return items[::-1][:500]

    def clear_history(self):
        HISTORY.write_text("", encoding="utf-8")
        return True

    # ------------- dictionary
    def get_dictionary(self):
        rows = []
        p = core.ROOT / "dictionary.txt"
        if p.exists():
            for line in p.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    a, b = line.split("=", 1)
                    rows.append([a.strip(), b.strip()])
        return rows

    def save_dictionary(self, rows):
        lines = ["# VoiceType: heard = written"]
        lines += [f"{a} = {b}" for a, b in rows if a.strip()]
        (core.ROOT / "dictionary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True

    # ------------- settings
    def get_settings(self):
        s = dict(self._engine.cfg)
        s["autostart"] = os.path.exists(STARTUP_LNK)
        s["system_dark"] = system_dark()
        return s

    def list_mics(self):
        try:
            import sounddevice as sd
            api_idx = sd.query_devices(kind="input")["hostapi"]
            return [{"id": i, "name": d["name"]} for i, d in enumerate(sd.query_devices())
                    if d["max_input_channels"] > 0 and d["hostapi"] == api_idx]
        except Exception:
            log.exception("mics")
            return []

    def save_settings(self, patch):
        eng = self._engine
        if "autostart" in patch:
            self._set_autostart(bool(patch.pop("autostart")))
        if "hotkey" in patch:
            try:
                eng.hotkey = eng.parse_key(patch["hotkey"])
            except ValueError as e:
                return {"error": str(e)}
        if "model" in patch and patch["model"] != eng.cfg.get("model"):
            eng.reload_model(patch["model"])
        if "overlay" in patch:
            eng.overlay.enabled = bool(patch["overlay"])
        eng.cfg.update(patch)
        if "ui_lang" in patch:
            eng.overlay.busy_text = eng.s("busy")
            self._out.put(lambda: tray_rebuild(eng))
        cfg = {k: v for k, v in eng.cfg.items() if k not in ("autostart", "system_dark")}
        with open(core.ROOT / "config.json", "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return {"ok": True}

    def _set_autostart(self, on):
        if on:
            pyw = core.ROOT / ".venv" / "Scripts" / "VoiceType.exe"
            if not pyw.exists():
                pyw = core.ROOT / ".venv" / "Scripts" / "pythonw.exe"
            appf = core.ROOT / "app" / "app.py"
            ico = core.ROOT / "voicetype.ico"
            run_ps(f"""$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{STARTUP_LNK}')
$s.TargetPath='{pyw}'; $s.Arguments='"{appf}" --tray'; $s.WorkingDirectory='{core.ROOT}'
$s.IconLocation='{pyw},0'; $s.Save()""")
        elif os.path.exists(STARTUP_LNK):
            os.remove(STARTUP_LNK)


# ======================================================================= tray
_tray = None
_tray_actions = {}


def _tray_menu(eng):
    from pystray import Menu, MenuItem as Item
    return Menu(Item(eng.s("open"), _tray_actions["show"], default=True),
                Item(eng.s("quit"), _tray_actions["quit"]))


def tray_update(state, eng):
    if _tray is not None:
        try:
            key = {"busy": "proc"}.get(state, state)
            _tray.icon = make_icon(COLORS.get(state, COLORS["ready"]))
            _tray.title = f"VoiceType — {eng.s(key)}"
        except Exception:
            pass


def tray_rebuild(eng):
    if _tray is not None:
        _tray.menu = _tray_menu(eng)
        try:
            _tray.update_menu()
        except Exception:
            pass


def start_tray(eng, show, quit_):
    global _tray
    import pystray
    _tray_actions.update(show=lambda *_: show(), quit=lambda *_: quit_())
    _tray = pystray.Icon("VoiceType", make_icon(COLORS["loading"]), "VoiceType", _tray_menu(eng))
    _tray.run_detached()


# ======================================================================= single instance
def already_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5) as s:
            s.sendall(b"show")
        return True
    except OSError:
        return False


_srv = None


def listen_for_show(show):
    global _srv
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", PORT))
    srv.listen(2)
    _srv = srv

    def loop():
        while True:
            conn, _ = srv.accept()
            with conn:
                if conn.recv(16) == b"show":
                    show()
    threading.Thread(target=loop, daemon=True).start()


# ======================================================================= mini recorder (floating pill)
class MiniApi:
    """JS API of the small floating recorder window."""

    def __init__(self, api):
        self._api = api
        self._ctl = None

    def init(self):
        cfg = self._api._engine.cfg
        th = cfg.get("theme", "system")
        return {"dark": th == "dark" or (th == "system" and system_dark()), "lang": cfg.get("ui_lang", "ru")}

    def status(self, since_peak=0):
        st = self._api._long.status(int(since_peak), 10 ** 9)
        out = {k: st[k] for k in ("state", "elapsed", "peaks", "npeaks")}
        out["visible"] = bool(self._ctl and self._ctl.shown)
        return out

    def pause(self):
        self._api._long.pause()

    def resume(self):
        self._api._long.resume()

    def stop(self):
        self._api._long.open_after = True
        self._api._long.stop()

    def open_main(self):
        threading.Thread(target=self._api._show_main, daemon=True).start()

    def drag(self):
        if self._ctl:
            self._ctl.drag()


class MiniController:
    """Shows the pill while a long recording runs and the main window is minimized or hidden."""

    W, H, MARGIN = 340, 64, 24

    def __init__(self, api, main_hwnd):
        import ctypes
        from ctypes import wintypes
        self.api, self.main = api, main_hwnd
        self.ct, self.wt, self.u32 = ctypes, wintypes, ctypes.windll.user32
        self.u32.GetForegroundWindow.restype = ctypes.c_void_p
        self.u32.GetAncestor.restype = ctypes.c_void_p
        self.u32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self.hwnd = None
        self.shown = False
        self.win = WinCtl()

    def attach(self, hwnd):
        self.hwnd = hwnd
        self.win.attach(hwnd)
        u = self.u32
        ex = u.GetWindowLongW(hwnd, -20)
        # tool window: no taskbar button, not in Alt+Tab; never takes focus
        ex = (ex | 0x80 | 0x08000000) & ~0x40000
        u.SetWindowLongW(hwnd, -20, ex)

    def _same_dpi_context(self):
        """Use the window's own DPI mode on this thread so monitor and window coordinates match."""
        try:
            u = self.u32
            u.GetWindowDpiAwarenessContext.restype = self.ct.c_void_p
            u.GetWindowDpiAwarenessContext.argtypes = [self.wt.HWND]
            u.SetThreadDpiAwarenessContext.restype = self.ct.c_void_p
            u.SetThreadDpiAwarenessContext.argtypes = [self.ct.c_void_p]
            u.SetThreadDpiAwarenessContext(u.GetWindowDpiAwarenessContext(self.hwnd))
        except Exception:
            pass

    def _place(self):
        self._same_dpi_context()
        l, t, r, b = self.win._rect()
        w, h = r - l, b - t
        # work area of the monitor with the main window
        class MI(self.ct.Structure):
            _fields_ = [("cbSize", self.wt.DWORD), ("rcMonitor", self.wt.RECT),
                        ("rcWork", self.wt.RECT), ("dwFlags", self.wt.DWORD)]
        mi = MI()
        mi.cbSize = self.ct.sizeof(MI)
        self.u32.GetMonitorInfoW(self.u32.MonitorFromWindow(self.main, 2), self.ct.byref(mi))
        wa = mi.rcWork
        pos = self.api._engine.cfg.get("mini_pos")
        x = y = None
        if isinstance(pos, list) and len(pos) == 2:
            x, y = int(pos[0]), int(pos[1])
            vx, vy = self.u32.GetSystemMetrics(76), self.u32.GetSystemMetrics(77)
            vw, vh = self.u32.GetSystemMetrics(78), self.u32.GetSystemMetrics(79)
            if not (vx <= x <= vx + vw - 40 and vy <= y <= vy + vh - 40):
                x = y = None
        if x is None:
            m = max(12, int(round(h * 0.375)))  # ~24 px at 64 px height, any DPI
            x, y = wa.right - w - m, wa.bottom - h - m
        ok = self.u32.SetWindowPos(self.hwnd, -1, x, y, 0, 0, 0x0001 | 0x0010)  # TOPMOST, NOSIZE, NOACTIVATE
        log.info("Мини-плеер: %dx%d в (%d, %d), рабочая область %d,%d-%d,%d, ok=%s",
                 w, h, x, y, wa.left, wa.top, wa.right, wa.bottom, ok)

    def show(self):
        # show first, then move: WinForms re-centers the form when it becomes visible
        self.u32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
        self._place()
        self.u32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
        self.shown = True

    def hide(self):
        self.u32.ShowWindow(self.hwnd, 0)
        self.shown = False

    def drag(self):
        self._same_dpi_context()
        self.win.drag()
        l, t, r, b = self.win._rect()
        self.api._engine.cfg["mini_pos"] = [l, t]
        try:
            self.api.save_settings({"mini_pos": [l, t]})
        except Exception:
            log.exception("mini pos")

    def _main_is_active(self):
        """True while the user works in VoiceType (its window or its own dialogs)."""
        fg = self.u32.GetForegroundWindow()
        if not fg:
            return True  # e.g. while Alt+Tab switcher is open; keep the current state
        root = self.u32.GetAncestor(fg, 3)  # GA_ROOTOWNER
        return fg in (self.main, self.hwnd) or root in (self.main, self.hwnd)

    def loop(self):
        while True:
            try:
                rec = self.api._long.state in ("rec", "paused", "finishing")
                away = bool(self.u32.IsIconic(self.main)) or not self.u32.IsWindowVisible(self.main) \
                    or not self._main_is_active()
                want = rec and away and self.api._engine.cfg.get("mini_recorder", True)
                if want and not self.shown:
                    self.show()
                elif not want and self.shown:
                    self.hide()
            except Exception:
                log.exception("mini loop")
            time.sleep(0.3)


# ======================================================================= main
def start_watchdog():
    """If Python stops running for 15 s, faulthandler (a C thread, no GIL needed) writes
    every thread's stack to logs/hang.log, so a freeze can be diagnosed."""
    import faulthandler
    f = open(core.ROOT / "logs" / "hang.log", "a", encoding="utf-8")
    faulthandler.enable(file=f)

    def beat():
        while True:
            faulthandler.cancel_dump_traceback_later()
            faulthandler.dump_traceback_later(15, file=f)
            time.sleep(3)
    threading.Thread(target=beat, daemon=True).start()


def main():
    tray_mode = "--tray" in sys.argv
    start_watchdog()
    if already_running():
        return
    import webview
    from webview.dom import _dnd_state

    api = Api()
    cfg = api._engine.cfg
    dark = cfg.get("theme") == "dark" or (cfg.get("theme", "system") == "system" and system_dark())
    window = webview.create_window(
        "VoiceType", url=str(core.ROOT / "ui" / "index.html"), js_api=api,
        width=1080, height=720, min_size=(760, 520), hidden=tray_mode,
        frameless=True, easy_drag=False, shadow=True,
        background_color="#191919" if dark else "#FFFFFF")
    api._window = window
    mini_api = MiniApi(api)
    mini = webview.create_window(
        "VoiceType mini", url=str(core.ROOT / "ui" / "mini.html"), js_api=mini_api,
        width=MiniController.W, height=MiniController.H, hidden=True, frameless=True, easy_drag=False,
        on_top=True, focus=False, resizable=False, shadow=True,
        background_color="#252525" if dark else "#FFFFFF")
    mini.events.closing += lambda: False

    def show():
        # Bring the window forward with plain Win32 calls: they release the GIL, so the UI thread
        # can run pywebview's Python event handlers meanwhile (toggling window.on_top from a
        # background thread deadlocked the app).
        window.show()
        try:
            import ctypes
            u32 = ctypes.windll.user32
            h = api._win.hwnd
            if h:
                if u32.IsIconic(h):
                    u32.ShowWindow(h, 9)  # SW_RESTORE
                u32.SetForegroundWindow(h)
        except Exception:
            log.exception("show")

    def quit_():
        log.info("Выход")
        api._long.emergency_close()
        try:
            _tray.stop()
        except Exception:
            pass
        os._exit(0)

    def on_closing():
        threading.Timer(0.01, window.hide).start()
        return False  # keep running in tray

    window.events.closing += on_closing
    api._show_main = show
    listen_for_show(show)
    start_tray(api._engine, show, quit_)

    def bind(win):
        # lets WebView2 hand over real paths of dropped files (see take_dropped)
        _dnd_state["num_listeners"] = max(1, _dnd_state["num_listeners"])
        try:
            for _ in range(100):
                if getattr(win, "native", None) is not None:
                    break
                time.sleep(0.05)
            api._win.attach(int(str(win.native.Handle.ToInt64())))
        except Exception:
            log.exception("hwnd")
        api._engine.start()
        def start_mini():
            try:
                for _ in range(1200):  # up to 60 s: on a busy PC the second window can be slow
                    if getattr(mini, "native", None) is not None:
                        break
                    time.sleep(0.05)
                if getattr(mini, "native", None) is None:
                    log.error("Мини-плеер: окно не создалось")
                    return
                ctl = MiniController(api, api._win.hwnd)
                ctl.attach(int(str(mini.native.Handle.ToInt64())))
                mini_api._ctl = ctl
                ctl.loop()
            except Exception:
                log.exception("mini recorder")
        threading.Thread(target=start_mini, daemon=True).start()
        if not tray_mode:
            time.sleep(1.0)
            show()

    webview.start(bind, window, storage_path=str(core.ROOT / "cache" / "webview"))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("Фатальная ошибка")
        message_box(f"VoiceType упал:\n{e}\n\nПодробности в logs\\app.log")
