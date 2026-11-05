"""Builds VoiceType-Setup.exe with NSIS (makensis must be on PATH; works on Linux and Windows).

    python installer/build.py

Draws the installer artwork from the app logo, then compiles installer/VoiceType.nsi.
"""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "app"))
import core  # noqa: E402

FONT_DIRS = ["/usr/share/fonts/opentype/inter", "C:/Windows/Fonts"]


def font(names, size):
    from PIL import ImageFont
    for d in FONT_DIRS:
        for n in names:
            p = Path(d) / n
            if p.exists():
                return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def art():
    from PIL import Image, ImageDraw
    bg, ink, grey = (251, 251, 250), (55, 53, 47), (120, 119, 116)
    bold = ["InterDisplay-SemiBold.otf", "Inter-SemiBold.otf", "segoeuib.ttf"]
    reg = ["Inter-Regular.otf", "segoeui.ttf"]

    # welcome / finish page: 164x314, drawn at 2x for smooth text
    s = 2
    w = Image.new("RGB", (164 * s, 314 * s), bg)
    d = ImageDraw.Draw(w)
    logo = core.draw_logo(64 * s)
    w.paste(logo, ((164 * s - logo.width) // 2, 92 * s), logo)
    f = font(bold, 19 * s)
    t = "VoiceType"
    tw = d.textlength(t, font=f)
    d.text(((164 * s - tw) / 2, 172 * s), t, font=f, fill=ink)
    f2 = font(reg, 10 * s)
    for i, line in enumerate(("Голос в текст", "локально и бесплатно")):
        lw = d.textlength(line, font=f2)
        d.text(((164 * s - lw) / 2, (200 + i * 15) * s), line, font=f2, fill=grey)
    w.resize((164, 314), Image.LANCZOS).save(HERE / "welcome.bmp")

    # header strip: 150x57, logo on the right
    h = Image.new("RGB", (150 * s, 57 * s), (255, 255, 255))
    lg = core.draw_logo(36 * s)
    h.paste(lg, (150 * s - lg.width - 10 * s, (57 * s - lg.height) // 2), lg)
    h.resize((150, 57), Image.LANCZOS).save(HERE / "header.bmp")


def main():
    art()
    version = (ROOT / "version.txt").read_text(encoding="utf-8").strip()
    makensis = shutil.which("makensis") or r"C:\Program Files (x86)\NSIS\makensis.exe"
    r = subprocess.run([makensis, "-V2", f"-DVERSION={version}", str(HERE / "VoiceType.nsi")])
    if r.returncode:
        sys.exit(r.returncode)
    print("Built", ROOT / "VoiceType-Setup.exe", f"({(ROOT / 'VoiceType-Setup.exe').stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
