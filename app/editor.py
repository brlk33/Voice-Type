"""Text editor model: fixes recognition mistakes (English terms written in Cyrillic, punctuation).

Runs locally: llama.cpp's llama-server (CUDA build) + a small instruct model (Qwen3-4B, GGUF).
The server starts on first use and is stopped after a few idle minutes to free video memory.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import core

log = logging.getLogger("voicetype")

ROOT = core.ROOT
LLAMA_DIR = ROOT / "tools" / "llama"
MODEL_DIR = ROOT / "models" / "editor"
PORT = 47654
NO_WINDOW = core.NO_WINDOW
# talk to the local server directly: a system proxy (VPN apps) must not intercept 127.0.0.1
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# where to look for the model (first that works wins)
MODEL_REPOS = ["lmstudio-community/Qwen3-4B-Instruct-2507-GGUF", "unsloth/Qwen3-4B-Instruct-2507-GGUF"]
MODEL_QUANT = "Q4_K_M"
HF_HOSTS = ["https://huggingface.co", "https://hf-mirror.com"]
LLAMA_RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
UA = {"User-Agent": "VoiceType"}


# ---------------------------------------------------------------- install
def model_path():
    files = sorted(MODEL_DIR.glob("*.gguf")) if MODEL_DIR.exists() else []
    return files[0] if files else None


def server_exe():
    for p in LLAMA_DIR.rglob("llama-server.exe") if LLAMA_DIR.exists() else []:
        return p
    return None


def installed():
    return bool(model_path() and server_exe())


def _get_json(url):
    req = urllib.request.Request(url, headers={**UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _download(url, dest: Path, progress, label, share=(0, 1)):
    """Download with resume; progress(fraction 0..1 overall, label)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = dict(UA)
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        if have and r.status != 206:  # server ignored Range: start over
            have = 0
        total = int(r.headers.get("Content-Length") or 0) + have
        mode = "ab" if have else "wb"
        done = have
        last = 0
        with open(part, mode) as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total and time.time() - last > 0.3:
                    last = time.time()
                    progress(share[0] + share[1] * done / total, label)
    os.replace(part, dest)


def _install_llama(progress):
    # the "latest" release can be a nightly placeholder: look through recent releases
    rels = _get_json(LLAMA_RELEASES + "?per_page=15")
    pick, seen = None, []
    for rel in rels:
        for a in rel.get("assets", []):
            seen.append(a["name"])
            if re.fullmatch(r"llama-.*-bin-win-cuda-?12[\d.]*-x64\.zip", a["name"]):
                pick = a
                break
        if pick:
            break
    if not pick:
        raise RuntimeError("Не нашёл сборку llama.cpp для CUDA 12. Файлы: " + ", ".join(seen[:30])[:400])
    zpath = ROOT / "cache" / pick["name"]
    _download(pick["browser_download_url"], zpath, progress, "llama.cpp", (0.0, 0.08))
    if LLAMA_DIR.exists():
        shutil.rmtree(LLAMA_DIR, ignore_errors=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(LLAMA_DIR)
    zpath.unlink(missing_ok=True)
    log.info("llama.cpp: %s", pick["name"])


def _install_model(progress):
    errors = []
    for host in HF_HOSTS:
        for repo in MODEL_REPOS:
            try:
                info = _get_json(f"{host}/api/models/{repo}")
                files = [s["rfilename"] for s in info.get("siblings", [])
                         if s["rfilename"].lower().endswith(".gguf") and MODEL_QUANT.lower() in s["rfilename"].lower()]
                if not files:
                    errors.append(f"{repo}: нет {MODEL_QUANT}")
                    continue
                name = files[0]
                _download(f"{host}/{repo}/resolve/main/{name}", MODEL_DIR / Path(name).name,
                          progress, "Qwen3-4B", (0.08, 0.92))
                log.info("Модель редактора: %s/%s", repo, name)
                return
            except Exception as e:
                log.info("Модель через %s/%s не скачалась: %s", host, repo, e)
                errors.append(f"{host}/{repo}: {e}")
    raise RuntimeError("Не удалось скачать модель редактора. " + "; ".join(errors)[-400:])


_install_state = {"running": False, "progress": 0.0, "label": "", "error": ""}


def install_status():
    return dict(_install_state, installed=installed())


def install_async(on_done=None):
    if _install_state["running"]:
        return
    _install_state.update(running=True, progress=0.0, label="", error="")

    def progress(frac, label):
        _install_state.update(progress=round(min(1.0, frac), 3), label=label)

    def work():
        try:
            if not server_exe():
                _install_llama(progress)
            if not model_path():
                _install_model(progress)
            progress(1.0, "")
            SERVER.ensure()  # start once to check that the GPU is used
        except Exception as e:
            log.exception("Установка редактора")
            _install_state["error"] = str(e)
        finally:
            _install_state["running"] = False
            if on_done:
                on_done()
    threading.Thread(target=work, daemon=True).start()


def uninstall():
    SERVER.stop()
    shutil.rmtree(MODEL_DIR, ignore_errors=True)
    shutil.rmtree(LLAMA_DIR, ignore_errors=True)


# ---------------------------------------------------------------- server
def _kill_orphans():
    """A server left over from a previous run (app was killed) holds the port and video memory."""
    if os.name != "nt":
        return
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'llama-server.exe' -and "
          f"$_.ExecutablePath -like '{LLAMA_DIR}*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, creationflags=NO_WINDOW)


class Server:
    IDLE_STOP = 10 * 60  # free the video memory after 10 idle minutes

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.last_use = 0.0
        self.gpu = None
        threading.Thread(target=self._idle_watch, daemon=True).start()

    def _alive(self):
        if not self.proc or self.proc.poll() is not None:
            return False
        try:
            with LOCAL.open(f"http://127.0.0.1:{PORT}/health", timeout=1) as r:
                return r.status == 200
        except Exception:
            return False

    def ensure(self):
        with self.lock:
            self.last_use = time.time()
            if self._alive():
                return
            exe, model = server_exe(), model_path()
            if not exe or not model:
                raise RuntimeError("Редактор не установлен")
            self.stop_locked()
            _kill_orphans()
            env = dict(os.environ)
            # CUDA runtime/cuBLAS DLLs come with PyTorch
            torch_lib = ROOT / ".venv" / "Lib" / "site-packages" / "torch" / "lib"
            env["PATH"] = os.pathsep.join([str(exe.parent), str(torch_lib), env.get("PATH", "")])
            logf = open(ROOT / "logs" / "editor.log", "w", encoding="utf-8", errors="replace")
            self.proc = subprocess.Popen(
                [str(exe), "-m", str(model), "--host", "127.0.0.1", "--port", str(PORT),
                 "-ngl", "99", "-c", "8192", "-np", "1", "--no-webui"],
                cwd=str(exe.parent), env=env, stdout=logf, stderr=subprocess.STDOUT,
                creationflags=NO_WINDOW)
            t0 = time.time()
            while time.time() - t0 < 90:
                if self.proc.poll() is not None:
                    raise RuntimeError("Сервер редактора завершился, см. logs/editor.log")
                try:
                    with LOCAL.open(f"http://127.0.0.1:{PORT}/health", timeout=1) as r:
                        if r.status == 200:
                            break
                except Exception:
                    pass
                time.sleep(0.3)
            else:
                raise RuntimeError("Сервер редактора не запустился за 90 с")
            try:
                txt = (ROOT / "logs" / "editor.log").read_text(encoding="utf-8", errors="replace")
                self.gpu = True if "CUDA" in txt or "cuda" in txt else None
            except Exception:
                pass
            log.info("Редактор запущен за %.1f с, GPU: %s", time.time() - t0, self.gpu)

    def stop_locked(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except Exception:
                self.proc.kill()
        self.proc = None

    def stop(self):
        with self.lock:
            self.stop_locked()

    def _idle_watch(self):
        while True:
            time.sleep(30)
            if self.proc and time.time() - self.last_use > self.IDLE_STOP:
                log.info("Редактор выгружен из памяти (простой)")
                self.stop()

    def chat(self, messages, max_tokens):
        self.ensure()
        body = json.dumps({"messages": messages, "temperature": 0.1, "top_p": 0.9,
                           "max_tokens": max_tokens, "stream": False}).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        with LOCAL.open(req, timeout=180) as r:
            out = json.loads(r.read().decode("utf-8"))
        self.last_use = time.time()
        return out["choices"][0]["message"]["content"]


SERVER = Server()

# ---------------------------------------------------------------- editing
SYSTEM = """Ты — корректор автоматической расшифровки русской речи. Тебе дают сырой текст, ты возвращаешь его исправленную версию.

Исправляй ТОЛЬКО:
1. Английские названия программ, сервисов, клавиш и терминов, которые распознались кириллицей или с ошибками. Пиши их правильно латиницей: «афтер эффектс», «Avtor Effects», «Autoreffects» → After Effects; «фигма» → Figma; «премьер» → Premiere Pro; «альт таб» → Alt+Tab; «контрол шифт» → Ctrl+Shift; «фпс» → FPS.
2. Явные ошибки распознавания похожих по звучанию слов, если правильное слово очевидно из контекста.
3. Пунктуацию и заглавные буквы.

Строго запрещено: перефразировать, менять порядок слов, сокращать, добавлять новые мысли, отвечать на вопросы из текста, добавлять комментарии, кавычки или пояснения. Если исправлять нечего — верни текст без изменений.
Верни только исправленный текст."""

EXAMPLES = [
    ("Открываю авторэффектс и создаю композицию 1080 на 1920, фпс 30.",
     "Открываю After Effects и создаю композицию 1080 на 1920, FPS 30."),
    ("потом жму альт таб и перехожу в фигму там копирую кадр",
     "Потом жму Alt+Tab и перехожу в Figma, там копирую кадр."),
]


def _messages(text, terms):
    sys = SYSTEM
    if terms:
        sys += "\n\nСловарь пользователя (пиши эти слова именно так): " + ", ".join(terms)
    msgs = [{"role": "system", "content": sys}]
    for a, b in EXAMPLES:
        msgs += [{"role": "user", "content": a}, {"role": "assistant", "content": b}]
    msgs.append({"role": "user", "content": text})
    return msgs


def _sane(src, out):
    """Reject answers that are not an edit of the source (too short/long, chatty)."""
    if not out:
        return False
    ls, lo = len(src), len(out)
    if lo < ls * 0.7 or lo > ls * 1.35 + 20:
        return False
    a = set(re.findall(r"[а-яё]{4,}", src.lower()))
    b = set(re.findall(r"[а-яё]{4,}", out.lower()))
    if a and len(a & b) / len(a) < 0.6:  # too many Russian words changed -> rephrased
        return False
    return True


def _split(text, limit=900):
    """Split long text on sentence ends into pieces <= limit chars."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?…])\s+", text):
        if cur and len(cur) + len(sent) + 1 > limit:
            parts.append(cur)
            cur = sent
        else:
            cur = (cur + " " + sent).strip()
    if cur:
        parts.append(cur)
    out = []
    for p in parts:  # sentences without punctuation (YouTube): cut by words
        while len(p) > limit * 1.5:
            cut = p.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            out.append(p[:cut])
            p = p[cut:].strip()
        out.append(p)
    return out


def edit(text: str, terms=()) -> str:
    """Return the corrected text, or the original if the editor is missing or misbehaves."""
    text = (text or "").strip()
    if not text or not installed():
        return text
    out = []
    for piece in _split(text):
        try:
            res = SERVER.chat(_messages(piece, list(terms)), max_tokens=int(len(piece) * 0.8) + 64).strip()
            res = re.sub(r"^(исправленный текст|ответ)\s*:\s*", "", res, flags=re.I).strip().strip("«»\"")
            out.append(res if _sane(piece, res) else piece)
            if not _sane(piece, res):
                log.info("Редактор: ответ отклонён (%d→%d символов)", len(piece), len(res))
        except Exception as e:
            log.warning("Редактор недоступен: %s", e)
            out.append(piece)
    return " ".join(out)
