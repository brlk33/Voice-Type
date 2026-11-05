"""Keep the install small.

- PyTorch ships ~0.9 GB of files that only matter for compiling C++ extensions
  (torch/lib/*.lib, torch/include). VoiceType never compiles anything, so they go.
- uv's download cache is not needed once the packages are installed.

Run by install.bat / gpu.bat after installing packages, and once by the app after an update.
Also: which graphics card is there (for the "GPU acceleration" switch).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / ".venv" / "Lib" / "site-packages"
TORCH = SITE / "torch"


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def slim(cache=True) -> int:
    """Delete what isn't needed at run time. Returns bytes freed."""
    freed = 0
    for lib in list((TORCH / "lib").glob("*.lib")) + list((SITE / "torchaudio" / "lib").glob("*.lib")):
        try:
            n = lib.stat().st_size
            lib.unlink()
            freed += n
        except OSError:
            pass
    for d in (TORCH / "include", TORCH / "share" / "cmake"):
        if d.exists():
            n = _size(d)
            shutil.rmtree(d, ignore_errors=True)
            freed += n - _size(d)
    if cache:
        for d in (ROOT / "cache" / "uv",):
            if d.exists():
                n = _size(d)
                shutil.rmtree(d, ignore_errors=True)
                freed += n - _size(d)
    return freed


def torch_cuda() -> str:
    """CUDA version PyTorch was built with ('' for the processor-only build), read without importing torch."""
    try:
        for line in (TORCH / "version.py").read_text(encoding="utf-8").splitlines():
            if line.startswith("cuda"):
                v = line.split("=", 1)[1].strip().strip("'\"")
                return "" if v == "None" else v
    except OSError:
        pass
    return ""


def nvidia_gpu() -> str:
    """Name of the NVIDIA graphics card, or '' if there is none (or no driver)."""
    exe = shutil.which("nvidia-smi") or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
    if not os.path.exists(exe):
        return ""
    try:
        r = subprocess.run([exe, "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True,
                           timeout=8, creationflags=0x08000000 if os.name == "nt" else 0)
        return (r.stdout or "").strip().splitlines()[0].strip() if r.returncode == 0 and r.stdout.strip() else ""
    except Exception:
        return ""


if __name__ == "__main__":
    n = slim(cache="--keep-cache" not in sys.argv)
    print(f"Cleanup: freed {n / 1048576:.0f} MB")
