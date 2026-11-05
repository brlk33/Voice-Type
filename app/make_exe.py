"""Builds VoiceType.exe (own process name + icon) and the Desktop / Start menu shortcuts.

VoiceType.exe is the bundled windowless Python interpreter copied next to the venv,
with the VoiceType icon and name written into its resources.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

ROOT = core.ROOT
SCRIPTS = ROOT / ".venv" / "Scripts"
EXE = SCRIPTS / "VoiceType.exe"
ICO = ROOT / "voicetype.ico"
APP = ROOT / "app" / "app.py"
RCEDIT = ROOT / "tools" / "rcedit.exe"
RCEDIT_URL = "https://github.com/electron/rcedit/releases/download/v2.0.0/rcedit-x64.exe"


def base_python_dir():
    for line in (ROOT / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("=")
        if k.strip() == "home":
            return v.strip()
    raise RuntimeError("pyvenv.cfg has no home")


def step(msg):
    print("\n==> " + msg, flush=True)


def main():
    home = base_python_dir()
    step(f"Python: {home}")

    step("Stopping running VoiceType")
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*VoiceType*app.py*' "
          "-or $_.ExecutablePath -like '*VoiceType\\tools\\llama*' } "
          "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps])
    time.sleep(1.5)

    step("Creating VoiceType.exe")
    shutil.copy2(os.path.join(home, "pythonw.exe"), EXE)
    for dll in ("python311.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        src = os.path.join(home, dll)
        if os.path.exists(src):
            shutil.copy2(src, SCRIPTS / dll)

    step("Icon and name")
    if not RCEDIT.exists():
        RCEDIT.parent.mkdir(exist_ok=True)
        urllib.request.urlretrieve(RCEDIT_URL, RCEDIT)
    subprocess.run([str(RCEDIT), str(EXE), "--set-icon", str(ICO),
                    "--set-version-string", "FileDescription", "VoiceType",
                    "--set-version-string", "ProductName", "VoiceType",
                    "--set-version-string", "CompanyName", "VoiceType",
                    "--set-version-string", "InternalName", "VoiceType",
                    "--set-version-string", "OriginalFilename", "VoiceType.exe",
                    "--set-version-string", "LegalCopyright", "",
                    "--set-file-version", "1.0.0", "--set-product-version", "1.0.0"], check=True)

    step("Checking that VoiceType.exe runs the app's Python")
    probe = ROOT / "logs" / "exe_check.txt"
    probe.unlink(missing_ok=True)
    code = ("import sys, torch, webview, gigaam; "
            f"open(r'{probe}', 'w').write(sys.executable + ' | ' + torch.__version__ + ' | cuda=' + str(torch.cuda.is_available()))")
    subprocess.run([str(EXE), "-c", code], timeout=180)
    if not probe.exists():
        raise RuntimeError("VoiceType.exe could not import the libraries")
    print(probe.read_text())

    step("Shortcuts")
    desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
        desktop = buf.value or desktop
    except Exception:
        pass
    start_menu = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs")
    startup = os.path.join(start_menu, "Startup", "VoiceType.lnk")
    links = [(os.path.join(desktop, "VoiceType.lnk"), ""), (os.path.join(start_menu, "VoiceType.lnk"), "")]
    if os.path.exists(startup):
        links.append((startup, " --tray"))
    for lnk, extra in links:
        script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
                  f"$s.TargetPath='{EXE}';$s.Arguments='\"{APP}\"{extra}';"
                  f"$s.WorkingDirectory='{ROOT}';$s.IconLocation='{EXE},0';"
                  f"$s.Description='VoiceType';$s.Save()")
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])
        print(("ok   " if r.returncode == 0 else "FAIL ") + lnk)
    # refresh icon cache so the new icon shows up immediately
    subprocess.run(["ie4uinit.exe", "-ClearIconCache"], capture_output=True)
    subprocess.run(["ie4uinit.exe", "-show"], capture_output=True)

    step("Starting VoiceType")
    subprocess.Popen([str(EXE), str(APP)], cwd=str(ROOT), creationflags=0x00000008)  # DETACHED_PROCESS
    print("\nDONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\nERROR:", e)
        sys.exit(1)
