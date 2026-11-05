"""Run by install.bat: check GPU, download the model, make icon and desktop shortcut."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

core.setup_logging("install")
import numpy as np  # noqa: E402
import torch  # noqa: E402

print("torch", torch.__version__, "| CUDA:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("GPU:", torch.cuda.get_device_name(0))
else:
    print("ВНИМАНИЕ: видеокарта не видна, будет работать на процессоре.")

cfg = core.load_config()
print("Скачиваю и загружаю модель GigaAM (около 1 ГБ, один раз)...")
rec = core.Recognizer(cfg, progress=lambda p: print(f"  {p}%", flush=True))
print("Модель:", rec.model_name, "на", rec.device)
if rec.model_name != cfg["model"]:
    cfg["model"] = rec.model_name
    import json
    with open(core.ROOT / "config.json", "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("config.json: модель переключена на", rec.model_name)

print("Проверка распознавания и нарезки по паузам...")
rec.transcribe(np.zeros(core.SR, dtype=np.float32))
rec.chunks(np.zeros(30 * core.SR, dtype=np.float32))
core.decode_file  # noqa
print("ffmpeg:", core.ffmpeg_exe())

# icon + desktop shortcut
from app import make_icon, COLORS  # noqa: E402
ico = core.ROOT / "voicetype.ico"
make_icon(COLORS["ready"]).save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
pyw = core.ROOT / ".venv" / "Scripts" / "pythonw.exe"
appf = core.ROOT / "app" / "app.py"
ps = f"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\\VoiceType.lnk')
$s.TargetPath = '{pyw}'
$s.Arguments = '"{appf}"'
$s.WorkingDirectory = '{core.ROOT}'
$s.IconLocation = '{ico}'
$s.Save()
"""
r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps])
print("Ярлык на рабочем столе:", "создан" if r.returncode == 0 else "не удалось")
print("\nВСЁ ГОТОВО")
