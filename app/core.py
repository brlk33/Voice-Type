"""VoiceType core: audio decoding, GigaAM recognition, VAD chunking, dictionary."""
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SR = 16000
SHORT_LIMIT = 22 * SR      # GigaAM handles up to 25 s in one pass
CHUNK_MAX = 20 * SR        # max chunk length for long audio
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

log = logging.getLogger("voicetype")

DEFAULT_CONFIG = {
    "hotkey": "ctrl_r",
    "hotkey_system": "shift_r",  # dictate the computer's sound ("" = off)
    "rec_source": "mic",         # long recording: mic | system | both
    "mode": "hold",
    "model": "v3_e2e_rnnt",
    "device": "auto",
    "mic_device": None,
    "sounds": True,
    "add_trailing_space": True,
    "restore_clipboard": True,
    "file_timestamps": False,
    "overlay": True,
    "ui_lang": "ru",
    "theme": "system",
    "style": "formal",
    "paragraphs": True,
    "paragraph_pause": 1.2,
    "clean": True,
    "clean_fillers": False,
    "long_paragraphs": "long",
    "ui_zoom": 1.0,
    "mini_recorder": True,
    "editor": False,
    "editor_dictation": True,
    "editor_recording": True,
    "editor_files": True,
    "editor_youtube": True,
    "editor_terms": ["After Effects", "Premiere Pro", "Photoshop", "Illustrator", "Figma", "DaVinci Resolve",
                     "Adobe", "Alt+Tab", "Ctrl", "Shift", "FPS", "YouTube", "Telegram", "Notion"],
    "mini_pos": None,
    "auto_update": True,
    "lang_mode": "auto",  # with the English model installed: ru | auto | en
}


def setup_logging(name: str = "app") -> None:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    handlers = [logging.FileHandler(logs / f"{name}.log", encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    else:  # pythonw: no console, keep libraries from crashing on print()
        f = open(logs / f"{name}.stdout.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s")


def load_config() -> dict:
    path = ROOT / "config.json"
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(path, encoding="utf-8-sig") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:  # broken json -> defaults, but say so
        log.error("config.json не читается (%s), использую настройки по умолчанию", e)
    return cfg


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def decode_file(path: str) -> np.ndarray:
    """Any audio/video file -> mono float32 16 kHz."""
    cmd = [ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error",
           "-i", str(path), "-vn", "-f", "s16le", "-ac", "1",
           "-acodec", "pcm_s16le", "-ar", str(SR), "-"]
    res = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    if res.returncode != 0:
        raise RuntimeError("ffmpeg не смог прочитать файл: "
                           + res.stderr.decode("utf-8", "replace")[-500:])
    return np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def save_wav(path: str, audio: np.ndarray) -> None:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def fmt_time(samples: int) -> str:
    s = samples // SR
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------- dictionary
class Dictionary:
    """dictionary.txt: lines like `гитхаб = GitHub`. Reloaded when the file changes."""

    def __init__(self, path: Path = ROOT / "dictionary.txt"):
        self.path = path
        default = ROOT / "dictionary.default.txt"
        if not path.exists() and default.exists():  # first run: start from the bundled examples
            import shutil
            shutil.copy2(default, path)
        self._mtime = None
        self._rules = []

    def _reload(self):
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self._rules = []
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        rules = []
        with open(self.path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                src, dst = (p.strip() for p in line.split("=", 1))
                if not src:
                    continue
                pat = re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, src.split())) + r"(?!\w)",
                                 re.IGNORECASE)
                rules.append((len(src), pat, dst))
        rules.sort(key=lambda r: -r[0])  # longer phrases first
        self._rules = [(p, d) for _, p, d in rules]
        log.info("Словарь: %d замен", len(self._rules))

    def targets(self) -> set:
        self._reload()
        return {w for _, d in self._rules for w in re.findall(r"[\w-]+", d)}

    def apply(self, text: str) -> str:
        self._reload()
        for pat, dst in self._rules:
            text = pat.sub(lambda m: dst, text)
        return text


# ---------------------------------------------------------------- recognizer
GIGAAM_URL = "https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM"


def _md5(path):
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# checksums of files GigaAM's package doesn't pin itself (taken from known-good downloads)
EXTRA_MD5 = {"v3_e2e_rnnt_tokenizer.model": "3b3bf8370e882885d79731592fc99f98"}


def download(url, dest, progress=None, md5=None):
    """Download with fallbacks. Python's TLS can fail where Windows' own stack works
    (antivirus / VPN inspecting traffic, old certificate chains), so if urllib fails we
    try curl.exe (built into Windows 10+, uses the Windows certificate store) and then
    PowerShell. If every way fails on the certificate and the file's checksum is known, it is
    downloaded once more without certificate checks and kept only if the checksum matches,
    so the file is exactly the official one whatever the network did to TLS.
    Raises RuntimeError with all the reasons if nothing worked."""
    import shutil
    import subprocess
    import urllib.request
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    errors = []

    def via_urllib(ctx=None):
        req = urllib.request.Request(url, headers={"User-Agent": "VoiceType"})
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got, last = 0, -1
            while True:
                buf = r.read(1 << 20)
                if not buf:
                    break
                f.write(buf)
                got += len(buf)
                pct = int(got * 100 / total) if total else -1
                if progress and pct != last and pct % 5 == 0:
                    last = pct
                    progress(pct)

    def via_curl():
        curl = shutil.which("curl.exe") or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe")
        if not os.path.exists(curl):
            raise RuntimeError("curl.exe not found")
        r = subprocess.run([curl, "-fsSL", "--retry", "3", "--ssl-no-revoke", "-o", str(tmp), url],
                           capture_output=True, text=True, creationflags=0x08000000 if os.name == "nt" else 0)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "").strip()[-300:] or f"curl exit {r.returncode}")

    def via_powershell():
        ps = ("$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
              "[Net.ServicePointManager]::SecurityProtocol='Tls12';"
              f"Invoke-WebRequest -UseBasicParsing '{url}' -OutFile '{tmp}'")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "").strip()[-300:])

    def via_checksum():
        import ssl
        via_urllib(ssl._create_unverified_context())

    ways = [via_urllib] + ([via_curl, via_powershell] if os.name == "nt" else [])
    if md5:
        ways.append(via_checksum)
    for way in ways:
        for attempt in range(3 if way is via_checksum else 2):
            try:
                way()
                if md5:
                    got = _md5(tmp)
                    if got != md5:
                        raise RuntimeError(f"checksum mismatch ({got})")
                os.replace(tmp, dest)
                if way is via_checksum:
                    log.info("%s: скачан без проверки сертификата, контрольная сумма совпала", dest.name)
                return dest
            except Exception as e:
                errors.append(f"{way.__name__[4:]}: {e}")
                log.warning("Загрузка %s (%s, попытка %d): %s", url.rsplit("/", 1)[-1], way.__name__[4:], attempt + 1, e)
                try:
                    tmp.unlink()
                except OSError:
                    pass
                time.sleep(2)
    raise RuntimeError("download failed: " + " | ".join(errors[-3:]))


def fetch_gigaam(name, models_dir, progress=None):
    """Make sure models/<name>.ckpt (and the tokenizer for e2e models) is on disk and intact."""
    import gigaam
    hashes = getattr(gigaam, "_MODEL_HASHES", {})
    ckpt = Path(models_dir) / f"{name}.ckpt"
    if not ckpt.exists():
        log.info("Скачиваю %s ...", ckpt.name)
        download(f"{GIGAAM_URL}/{name}.ckpt", ckpt, progress, md5=hashes.get(name))
    if "e2e" in name or name == "v1_rnnt":
        tok = Path(models_dir) / f"{name}_tokenizer.model"
        if not tok.exists():
            download(f"{GIGAAM_URL}/{name}_tokenizer.model", tok, md5=EXTRA_MD5.get(tok.name))


class Recognizer:
    def __init__(self, cfg: dict, progress=None):
        import torch
        import gigaam
        self.torch = torch
        dev = cfg.get("device", "auto")
        if dev == "auto":
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = dev
        if dev == "cuda":
            log.info("GPU: %s", torch.cuda.get_device_name(0))
        else:
            log.warning("CUDA недоступна, работаю на процессоре (медленнее)")
        models_dir = str(ROOT / "models")
        wanted = cfg.get("model", "v3_e2e_rnnt")
        self.model = None
        for name in dict.fromkeys([wanted, "v3_e2e_ctc"]):
            try:
                log.info("Загружаю модель %s ...", name)
                fetch_gigaam(name, models_dir, progress)
                self.model = gigaam.load_model(name, device=dev, download_root=models_dir)
                self.model_name = name
                break
            except Exception:
                log.exception("Не удалось загрузить %s", name)
        if self.model is None:
            raise RuntimeError(
                "Не удалось скачать модель GigaAM. Похоже, сервер Сбера недоступен из вашей сети "
                "(часто мешает антивирус или VPN). Можно скачать вручную в браузере:\n"
                f"  {GIGAAM_URL}/{wanted}.ckpt\n"
                + (f"  {GIGAAM_URL}/{wanted}_tokenizer.model\n" if "e2e" in wanted else "")
                + f"и положить в папку {models_dir}, затем запустить установку снова.")
        self._lock = threading.Lock()
        self.english = None     # english.English, when the optional model is installed
        self.lang_mode = "ru"   # ru | auto | en
        self._vad = None
        self._vad_lock = threading.Lock()
        log.info("Модель %s готова на %s", self.model_name, dev)

    # -- one piece of audio up to ~25 s
    def _recognize_short(self, audio: np.ndarray) -> str:
        if len(audio) < SR // 4:
            return ""
        en = self.english
        if en is not None and self.lang_mode in ("auto", "en"):
            with self._lock:
                try:
                    if self.lang_mode == "en" or en.is_english(audio):
                        text = en.transcribe(audio)
                        if text:
                            return text
                except Exception:
                    log.exception("Английская модель")
        torch = self.torch
        m = self.model
        with self._lock, torch.inference_mode():
            try:
                wav = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32))
                wav = wav.to(m._device).to(m._dtype).unsqueeze(0)
                length = torch.full([1], wav.shape[-1], device=m._device)
                enc, enc_len = m.forward(wav, length)
                return m._decode(enc, enc_len, length)[0][0].strip()
            except (AttributeError, TypeError):
                # library internals changed -> go through the public API
                fd, tmp = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                try:
                    save_wav(tmp, audio)
                    res = m.transcribe(tmp)
                    return (getattr(res, "text", res) or "").strip()
                finally:
                    os.remove(tmp)

    def vad(self, audio: np.ndarray, **kw):
        """Speech timestamps (in samples). Thread-safe: the VAD model keeps internal state."""
        from silero_vad import get_speech_timestamps
        with self._vad_lock:
            return get_speech_timestamps(self.torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32)),
                                         self._vad_model(), sampling_rate=SR, **kw)

    def _vad_model(self):
        if self._vad is None:
            from silero_vad import load_silero_vad
            self._vad = load_silero_vad()
        return self._vad

    def chunks(self, audio: np.ndarray):
        """Split long audio on pauses into pieces <= CHUNK_MAX samples."""
        ts = self.vad(audio, min_silence_duration_ms=300, speech_pad_ms=150)
        pieces = []
        for seg in ts:
            st, en = int(seg["start"]), int(seg["end"])
            while en - st > CHUNK_MAX:
                pieces.append([st, st + CHUNK_MAX])
                st += CHUNK_MAX
            pieces.append([st, en])
        out = []
        for st, en in pieces:
            if out and en - out[-1][0] <= CHUNK_MAX and st - out[-1][1] < 2 * SR:
                out[-1][1] = en
            else:
                out.append([st, en])
        return out

    def dictation_paragraphs(self, audio: np.ndarray, pause_s: float = 1.2) -> list:
        """Split a dictation into paragraphs where the speaker paused for >= pause_s."""
        if len(audio) < int(SR * 2.5):
            text = self._recognize_short(audio)
            return [text] if text else []
        ts = self.vad(audio, min_silence_duration_ms=250, speech_pad_ms=120)
        if not ts:
            return []
        groups = [[ts[0]]]
        for seg in ts[1:]:
            gap = (seg["start"] - groups[-1][-1]["end"]) / SR + 0.24  # undo the padding
            if gap >= pause_s:
                groups.append([seg])
            else:
                groups[-1].append(seg)
        paras = []
        for g in groups:
            st = max(0, g[0]["start"] - int(0.1 * SR))
            en = min(len(audio), g[-1]["end"] + int(0.1 * SR))
            piece = audio[st:en]
            if len(piece) <= SHORT_LIMIT:
                text = self._recognize_short(piece)
            else:
                text = " ".join(t for t in (self._recognize_short(piece[a:b]) for a, b in self.chunks(piece)) if t)
            if text:
                paras.append(text)
        return paras

    def transcribe_segments(self, audio: np.ndarray, progress=None) -> list:
        """-> [{"t": start_seconds, "text": ...}, ...], one per paragraph."""
        if len(audio) <= SHORT_LIMIT:
            text = self._recognize_short(audio)
            if progress:
                progress(1, 1)
            return [{"t": 0.0, "end": len(audio) / SR, "text": text}] if text else []
        segs = []
        spans = self.chunks(audio)
        for i, (st, en) in enumerate(spans):
            text = self._recognize_short(audio[st:en])
            if text:
                segs.append({"t": st / SR, "end": en / SR, "text": text})
            if progress:
                progress(i + 1, len(spans))
        return segs

    def transcribe(self, audio: np.ndarray, progress=None, timestamps=False) -> str:
        if len(audio) <= SHORT_LIMIT:
            return self._recognize_short(audio)
        segs = self.transcribe_segments(audio, progress)
        return "\n".join(f"[{fmt_time(int(s['t'] * SR))}] {s['text']}" if timestamps else s["text"]
                         for s in segs)


def write_transcript(path: str, text: str) -> Path:
    """Write <name>.txt next to the source (or into transcripts/ if not writable)."""
    src = Path(path)
    target = src.with_suffix(".txt")
    if target.exists():
        target = src.with_name(src.stem + ".transcript.txt")
    try:
        target.write_text(text + "\n", encoding="utf-8")
    except OSError:
        (ROOT / "transcripts").mkdir(exist_ok=True)
        target = ROOT / "transcripts" / (src.stem + ".txt")
        target.write_text(text + "\n", encoding="utf-8")
    log.info("Готово: %s", target)
    return target


def transcribe_file(rec: Recognizer, dic: Dictionary, path: str, cfg: dict,
                    progress=None) -> Path:
    audio = decode_file(str(path))
    log.info("Файл %s: %.1f с", Path(path).name, len(audio) / SR)
    text = rec.transcribe(audio, progress=progress, timestamps=cfg.get("file_timestamps"))
    return write_transcript(path, dic.apply(text))


# ---------------------------------------------------------------- text style (Wispr Flow-like)
_CYR_CAP = re.compile(r"^[А-ЯЁ][а-яё]*$")


def _lower_word(word: str, protect: set) -> str:
    core_ = re.sub(r"[^\w-]", "", word)
    if core_ in protect or not _CYR_CAP.match(core_.split("-")[0] or "x"):
        return word  # names, brands, English words, acronyms stay as they are
    return word[0].lower() + word[1:]


def apply_style(text: str, style: str, protect=()) -> str:
    """formal: as recognized; casual: lowercase sentence starts, no final period;
    very_casual: lowercase, no periods and commas (keeps ? and !)."""
    if style not in ("casual", "very_casual") or not text:
        return text
    protect = set(protect)
    # lowercase the first word of the text and of every sentence
    text = re.sub(r"(?:^|(?<=[.!?…]\s))(\S+)", lambda m: _lower_word(m.group(1), protect), text)
    text = re.sub(r"\.(\s*)$", r"\1", text.rstrip()).rstrip()  # final period
    if style == "very_casual":
        text = re.sub(r"(?<!\.)\.(?!\.)\s*", " ", text)   # sentence periods (not "...")
        text = re.sub(r",\s*", " ", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
    return text


# ---------------------------------------------------------------- logo (same as in the app UI)
def draw_logo(size: int, bg=(55, 53, 47), fg=(255, 255, 255)):
    """Rounded square with four sound bars, rendered 4x and downscaled for smooth edges."""
    from PIL import Image, ImageDraw
    S = size * 4
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = round(S * 0.03)
    d.rounded_rectangle((pad, pad, S - pad, S - pad), radius=round(S * 0.24), fill=bg)
    # bars from the 14x14 sidebar icon, scaled to 56% of the tile
    box = S * 0.56
    ox = oy = (S - box) / 2
    u = box / 14
    for x, y, w, h in ((1, 5, 2, 4), (4.5, 2, 2, 10), (8, 3.5, 2, 7), (11.5, 5.5, 2, 3)):
        d.rounded_rectangle((ox + x * u, oy + y * u, ox + (x + w) * u, oy + (y + h) * u),
                            radius=u, fill=fg)
    return img.resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------- clean speech: fillers and repeats
_HESITATION = re.compile(r"^(э+|э-э+(-э+)*|ээ+|эм+|э-м+|м+|м-м+(-м+)*|мм+|хм+|а-а+(-а+)*|аа+|о-о+)$")
_KEEP_DOUBLE = {"очень", "да", "нет", "так", "много", "давно", "долго", "еле", "чуть", "тук", "ха", "ой", "ну"}
_PUNCT_END = re.compile(r"[.!?…]+$")


def _norm(tok: str) -> str:
    return re.sub(r"[^\w-]", "", tok.lower()).strip("-")


def _drop(tokens, i):
    """Remove token i, keeping a sentence end it carried; ", э-э, " collapses to nothing."""
    tok = tokens.pop(i)
    m = _PUNCT_END.search(tok)
    if i == 0:
        return
    prev = tokens[i - 1]
    if m and not re.search(r"[.!?…,;:]$", prev):
        tokens[i - 1] += m.group(0)
    elif m and prev.endswith(","):
        tokens[i - 1] = prev[:-1] + m.group(0)
    elif tok.endswith(",") and prev.endswith(","):
        tokens[i - 1] = prev[:-1]


def _drop2(tokens, i):
    """Remove a two-word filler at i, i+1 as one unit."""
    tail = re.search(r"[,.!?…;:]+$", tokens[i + 1])
    tokens[i] = re.sub(r"[,.!?…;:]+$", "", tokens[i]) + (tail.group(0) if tail else "")
    tokens.pop(i + 1)
    _drop(tokens, i)


def _remove_fillers(tokens):
    i = 0
    while i < len(tokens):
        n = _norm(tokens[i])
        nxt = _norm(tokens[i + 1]) if i + 1 < len(tokens) else ""
        prev = _norm(tokens[i - 1]) if i > 0 else ""
        prev_raw = tokens[i - 1] if i > 0 else "."
        sent_start = i == 0 or bool(re.search(r"[.!?…]$", prev_raw))
        two = f"{n} {nxt}"
        if two in ("как бы", "так сказать", "это самое") and _norm(tokens[i + 2] if i + 2 < len(tokens) else "") != "не":
            _drop2(tokens, i)
            continue
        if two == "в общем" and _norm(tokens[i + 2] if i + 2 < len(tokens) else "") != "и" and (sent_start or tokens[i + 1].endswith(",") or prev_raw.endswith(",")):
            _drop2(tokens, i)
            continue
        if n == "в общем-то":
            _drop(tokens, i)
            continue
        if n == "типа" and not re.search(r"(ого|его|ой|ого,)$", prev) and prev not in ("тот", "этот", "такой", "какой"):
            _drop(tokens, i)
            continue
        if n == "короче" and nxt != "чем" and prev not in ("гораздо", "намного", "ещё", "еще", "всё", "все", "чуть", "значительно"):
            _drop(tokens, i)
            continue
        if n == "ну" and sent_start:
            _drop(tokens, i)
            continue
        if n == "вот" and _PUNCT_END.search(tokens[i]) and prev_raw.endswith(","):
            _drop(tokens, i)
            continue
        i += 1
    return tokens


def clean_text(text: str, fillers: bool = False) -> str:
    """Remove hesitations (э-э, м-м), immediate word/phrase repeats and optionally filler words."""
    if not text:
        return text
    tokens = text.split()
    # 1) hesitation sounds
    i = 0
    while i < len(tokens):
        if _HESITATION.match(_norm(tokens[i]) or "x"):
            _drop(tokens, i)
        else:
            i += 1
    # 2) filler words
    if fillers:
        tokens = _remove_fillers(tokens)
    # 3) immediate repeats of 1..12 words ("я хотел я хотел сказать")
    out = []
    i = 0
    while i < len(tokens):
        skipped = False
        for n in range(12, 0, -1):
            if i + 2 * n > len(tokens):
                continue
            a = [_norm(t) for t in tokens[i:i + n]]
            b = [_norm(t) for t in tokens[i + n:i + 2 * n]]
            if a == b and all(a) and not (n == 1 and a[0] in _KEEP_DOUBLE) and not any(x.isdigit() for x in a) \
                    and not _PUNCT_END.search(tokens[i + n - 1]):
                i += n  # drop the first copy
                skipped = True
                break
        if not skipped:
            out.append(tokens[i])
            i += 1
    # 4) a sentence said again and continued: keep only the full version
    sents = re.split(r"(?<=[.!?…])\s+", " ".join(out))
    kept = []
    for k, sent in enumerate(sents):
        nxt = sents[k + 1] if k + 1 < len(sents) else ""
        a = " ".join(_norm(w) for w in sent.split())
        b = " ".join(_norm(w) for w in nxt.split())
        if nxt and len(a.split()) >= 3 and (b == a or b.startswith(a + " ")):
            continue
        kept.append(sent)
    text = " ".join(kept)
    # 5) tidy punctuation and sentence capitals
    text = re.sub(r"\s+([,.!?…;:])", r"\1", text)
    text = re.sub(r",\s*,+", ",", text)
    text = re.sub(r",([.!?…])", r"\1", text)
    text = re.sub(r"^[,;:\s]+", "", text)
    text = re.sub(r"([.!?…]\s+)[,;:]\s*", r"\1", text)
    text = re.sub(r"(^|[.!?…]\s+)([а-яёa-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    return text.strip()



# ---------------------------------------------------------------- joining recognition pieces into paragraphs
PARA_PAUSE = {"long": 2.5, "chunk": 0.0, "none": None}
SHORT_JOIN = 0.35  # a pause shorter than this is the middle of a sentence


def join_piece(prev: str, nxt: str, gap: float) -> str:
    """Glue two recognized pieces. On a short pause the model's period is usually wrong:
    'мы откроем. Настройки' -> 'мы откроем настройки'."""
    prev, nxt = prev.rstrip(), nxt.lstrip()
    if not prev:
        return nxt
    if not nxt:
        return prev
    first_sentence = re.split(r"(?<=[.!?…])\s", nxt, 1)[0]
    if gap < SHORT_JOIN and re.search(r"[^.]\.$", prev) and not first_sentence.endswith("?"):
        first = nxt.split(" ", 1)[0]
        if _CYR_CAP.match(re.sub(r"[^\w-]", "", first).split("-")[0] or "x"):
            prev = prev[:-1]
            nxt = nxt[0].lower() + nxt[1:]
    return prev + " " + nxt


def new_paragraph(mode: str, gap: float, cur_len: int) -> bool:
    if mode == "chunk":
        return True
    if mode == "none":
        return False
    return gap >= PARA_PAUSE["long"] or (cur_len > 1600 and gap >= 1.0)


def merge_segments(chunks: list, mode: str = "long") -> list:
    """[{t, end, text}] recognition pieces -> paragraphs [{t, end, text}]."""
    out = []
    for c in chunks:
        if not out:
            out.append(dict(c))
            continue
        last = out[-1]
        gap = c["t"] - last.get("end", c["t"])
        if new_paragraph(mode, gap, len(last["text"])):
            out.append(dict(c))
        else:
            last["text"] = join_piece(last["text"], c["text"], gap)
            last["end"] = c.get("end", c["t"])
    return out
