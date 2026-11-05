"""Optional English speech model (Whisper via faster-whisper).

Not installed by default. When installed, every piece of speech (between pauses) is checked:
English goes to Whisper, everything else to GigaAM. Whisper itself tells the language.

- with NVIDIA acceleration: Whisper large-v3-turbo (float16, ~1.6 GB) — very good English
- on the processor:         Whisper small (int8, ~0.5 GB) — lighter and fast enough
"""
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

import core

log = logging.getLogger("voicetype")
ROOT = core.ROOT
PKG = "faster-whisper==1.1.1"
MODELS = {  # device -> (folder, Hugging Face repo, compute type, approx size bytes)
    "cuda": ("whisper-large-v3-turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "float16", 1.62e9),
    "cpu": ("whisper-small", "Systran/faster-whisper-small", "int8", 0.49e9),
}
PKG_SIZE = 0.12e9
FILES = ("config.json", "model.bin", "tokenizer.json", "preprocessor_config.json",
         "vocabulary.json", "vocabulary.txt")


def _device():
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def model_dir():
    """Installed model folder (any of the two), or None."""
    for folder, *_ in MODELS.values():
        d = ROOT / "models" / folder
        if (d / "model.bin").exists() and (d / "config.json").exists():
            return d
    return None


def package_ok() -> bool:
    import importlib.util
    return importlib.util.find_spec("faster_whisper") is not None


def installed() -> bool:
    return model_dir() is not None and package_ok()


def disk_size() -> int:
    d = model_dir()
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) if d else 0


# ================================================================ install / remove
_state = {"running": False, "progress": 0.0, "label": "", "error": ""}


def plan():
    folder, repo, ct, size = MODELS[_device()]
    return {"model": folder.replace("whisper-", "Whisper "), "size": size + (0 if package_ok() else PKG_SIZE)}


def status():
    d = model_dir()
    return dict(_state, installed=installed(), model=(d.name.replace("whisper-", "Whisper ") if d else ""),
                size=disk_size(), plan=plan())


def _uv(*args):
    uv = ROOT / "tools" / "uv" / "uv.exe"
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    env = dict(os.environ, UV_CACHE_DIR=str(ROOT / "cache" / "uv"), UV_PYTHON_INSTALL_DIR=str(ROOT / "tools" / "python"))
    r = subprocess.run([str(uv), "pip", *args, "--python", str(py)], capture_output=True, text=True, env=env,
                       cwd=str(ROOT), creationflags=core.NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "uv")[-400:])


def install_async(on_done=None):
    if _state["running"]:
        return
    _state.update(running=True, progress=0.0, label="", error="")

    def run():
        import editor  # reuses its resumable downloader and Hugging Face mirrors
        try:
            if not package_ok():
                _state.update(label="faster-whisper", progress=0.02)
                _uv("install", PKG, "-c", str(ROOT / "constraints.txt"))
                import importlib
                importlib.invalidate_caches()
            folder, repo, ct, size = MODELS[_device()]
            dest = ROOT / "models" / folder
            errors = []
            for host in editor.HF_HOSTS:
                try:
                    info = editor._get_json(f"{host}/api/models/{repo}")
                    names = [s["rfilename"] for s in info.get("siblings", []) if s["rfilename"] in FILES]
                    big = [n for n in names if n == "model.bin"]
                    for n in [n for n in names if n != "model.bin"] + big:
                        share = (0.1, 0.9) if n == "model.bin" else (0.05, 0.05)
                        editor._download(f"{host}/{repo}/resolve/main/{n}", dest / n,
                                         lambda f, lbl: _state.update(progress=f, label=lbl), folder, share)
                    break
                except Exception as e:
                    errors.append(f"{host}: {e}")
                    log.info("Whisper через %s не скачался: %s", host, e)
            else:
                raise RuntimeError("; ".join(errors)[-400:])
            _state.update(progress=1.0, label="")
            log.info("Английская модель установлена: %s", folder)
            if on_done:
                on_done()
        except Exception as e:
            log.exception("Английская модель: установка")
            _state["error"] = str(e)[:300]
        finally:
            _state["running"] = False
    threading.Thread(target=run, daemon=True).start()


def remove(loaded: bool):
    """Delete the model now. The package can only be removed while it isn't loaded
    (Windows locks its DLLs), so if it is, that happens on the next start."""
    for folder, *_ in MODELS.values():
        shutil.rmtree(ROOT / "models" / folder, ignore_errors=True)
    if loaded:
        return "later"
    purge_package()
    return "done"


def purge_package():
    try:
        _uv("uninstall", "faster-whisper", "ctranslate2", "av", "tokenizers", "huggingface-hub")
        log.info("faster-whisper удалён")
    except Exception as e:
        log.warning("faster-whisper не удалился: %s", e)


# ================================================================ recognition
class English:
    """Whisper for English pieces + language check for every piece."""

    def __init__(self, device):
        if device == "cuda":
            try:  # cuBLAS / cuDNN come with the NVIDIA build of PyTorch; must be set before the import
                os.add_dll_directory(str(ROOT / ".venv" / "Lib" / "site-packages" / "torch" / "lib"))
            except Exception:
                pass
        from faster_whisper import WhisperModel
        d = model_dir()
        ct = "float16" if device == "cuda" else "int8"
        try:
            self.model = WhisperModel(str(d), device=device, compute_type=ct)
        except Exception:
            log.exception("Whisper на %s не запустился, пробую процессор", device)
            device, ct = "cpu", "int8"
            self.model = WhisperModel(str(d), device="cpu", compute_type="int8")
        self.name = d.name
        log.info("Английская модель %s готова (%s, %s)", d.name, device, ct)

    def is_english(self, audio) -> bool:
        """Russian vs English for one piece of speech. Very short pieces stay Russian."""
        if len(audio) < int(core.SR * 1.2):
            return False
        try:
            _, _, probs = self.model.detect_language(audio=audio)
        except Exception:
            log.exception("Whisper: язык")
            return False
        p = dict(probs)
        en, ru = p.get("en", 0.0), p.get("ru", 0.0)
        return en > 0.5 and en > ru * 2

    def transcribe(self, audio) -> str:
        segs, _ = self.model.transcribe(audio, language="en", beam_size=5, vad_filter=False,
                                        condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segs).strip()
