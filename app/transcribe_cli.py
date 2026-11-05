"""Transcribe files given on the command line (or chosen in a dialog)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

core.setup_logging("files")


def main():
    paths = sys.argv[1:]
    if not paths:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        paths = list(filedialog.askopenfilenames(title="Выберите аудио или видео"))
        root.destroy()
    if not paths:
        print("Файлы не выбраны.")
        return
    cfg = core.load_config()
    rec = core.Recognizer(cfg)
    dic = core.Dictionary()
    for i, p in enumerate(paths, 1):
        print(f"\n[{i}/{len(paths)}] {p}")
        try:
            out = core.transcribe_file(
                rec, dic, p, cfg,
                progress=lambda k, n: print(f"\r  {k * 100 // n}%", end="", flush=True))
            print(f"\n  -> {out}")
        except Exception as e:
            core.log.exception("Ошибка")
            print(f"\n  ОШИБКА: {e}")


if __name__ == "__main__":
    main()
