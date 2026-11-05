"""Auto-update from GitHub releases.

On start the app asks GitHub for the latest release. If it is newer than version.txt,
the release archive is downloaded and only program files are replaced: user data
(config.json, dictionary.txt, history.txt, transcripts/, logs/, models/, .venv/, tools/)
is never touched. New Python packages are installed automatically if requirements changed.
The running program keeps working; the new version starts on the next launch or after
"Restart" in the app.
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "brlk33/Voice-Type"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
UA = {"User-Agent": "VoiceType-updater", "Accept": "application/vnd.github+json"}

# files and folders the updater may replace (relative to the program folder)
MANAGED_DIRS = ("app", "ui")
MANAGED_FILES = ("requirements.txt", "constraints.txt", "install.bat", "update.bat", "start.bat",
                 "start_debug.bat", "transcribe_files.bat", "build_exe.bat", "voicetype.ico",
                 "dictionary.default.txt", "README.md", "version.txt")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

log = logging.getLogger("voicetype")


def local_version() -> str:
    try:
        return (ROOT / "version.txt").read_text(encoding="utf-8").strip() or "0"
    except FileNotFoundError:
        return "0"


def _parse(v: str):
    out = []
    for part in v.strip().lstrip("vV").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (4 - len(out)))


def is_newer(remote: str, local: str) -> bool:
    return _parse(remote) > _parse(local)


def latest_release(timeout=10):
    """-> {"version", "zip", "notes", "url"} or None."""
    req = urllib.request.Request(API, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:  # repository has no releases yet
            return None
        raise
    tag = data.get("tag_name") or ""
    if not tag:
        return None
    return {"version": tag.lstrip("vV"), "zip": data.get("zipball_url"),
            "notes": (data.get("body") or "")[:2000], "url": data.get("html_url")}


def _download(url, dest, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def _install_requirements():
    uv = ROOT / "tools" / "uv" / "uv.exe"
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not uv.exists() or not py.exists():
        return False
    env = dict(os.environ, UV_CACHE_DIR=str(ROOT / "cache" / "uv"),
               UV_PYTHON_INSTALL_DIR=str(ROOT / "tools" / "python"))
    r = subprocess.run([str(uv), "pip", "install", "--python", str(py), "-r", "requirements.txt",
                        "-c", "constraints.txt", "--upgrade-package", "yt-dlp"],
                       cwd=str(ROOT), env=env, capture_output=True, creationflags=NO_WINDOW, timeout=1800)
    if r.returncode != 0:
        log.error("Обновление: pip не прошёл: %s", r.stderr.decode("utf-8", "replace")[-1500:])
    return r.returncode == 0


def apply_release(rel) -> dict:
    """Download and install a release. Returns {"ok", "version", "deps"}."""
    work = Path(tempfile.mkdtemp(prefix="voicetype-update-"))
    try:
        zpath = work / "release.zip"
        _download(rel["zip"], zpath)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(work / "x")
        tops = [p for p in (work / "x").iterdir() if p.is_dir()]
        src = tops[0] if len(tops) == 1 else work / "x"
        if not (src / "app" / "app.py").exists():
            raise RuntimeError("в архиве нет app/app.py")
        old_req = (ROOT / "requirements.txt").read_bytes() if (ROOT / "requirements.txt").exists() else b""
        for d in MANAGED_DIRS:
            if (src / d).is_dir():
                for f in (src / d).rglob("*"):
                    if f.is_file() and "__pycache__" not in f.parts:
                        dst = ROOT / f.relative_to(src)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, dst)
        for name in MANAGED_FILES:
            if (src / name).is_file():
                shutil.copy2(src / name, ROOT / name)
        deps_ok = True
        if (ROOT / "requirements.txt").read_bytes() != old_req:
            log.info("Обновление: ставлю новые библиотеки")
            deps_ok = _install_requirements()
        (ROOT / "version.txt").write_text(rel["version"] + "\n", encoding="utf-8")
        log.info("Обновление до %s установлено", rel["version"])
        return {"ok": True, "version": rel["version"], "deps": deps_ok}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check(apply=True):
    """-> {"current", "latest", "updated", "error", ...}"""
    cur = local_version()
    res = {"current": cur, "latest": cur, "updated": False, "error": ""}
    try:
        rel = latest_release()
        if not rel:
            return res
        res["latest"] = rel["version"]
        res["notes"] = rel.get("notes", "")
        if is_newer(rel["version"], cur) and apply:
            r = apply_release(rel)
            res.update(updated=r["ok"], deps=r["deps"])
    except Exception as e:
        log.warning("Проверка обновлений: %s", e)
        res["error"] = str(e)[:300]
    return res
