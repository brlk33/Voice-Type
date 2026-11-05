"""Small pill at the bottom of the screen while dictating (Wispr Flow style).

Never takes focus and lets clicks through, so pasting goes to the right window.
"""
import ctypes
import math
import threading
from collections import deque

IS_WIN = __import__("os").name == "nt"


class Overlay:
    def __init__(self, level_fn):
        self.level_fn = level_fn       # returns current mic level 0..1
        self.state = "idle"            # idle | rec | busy
        self.enabled = True
        self.busy_text = "Распознаю"
        if IS_WIN:
            threading.Thread(target=self._run, daemon=True).start()

    def set(self, state):
        self.state = state if self.enabled else "idle"

    def _run(self):
        try:
            self._loop()
        except Exception:
            import logging
            logging.getLogger("voicetype").exception("overlay")

    def _loop(self):
        import tkinter as tk
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        try:
            scale = user32.GetDpiForSystem() / 96.0
        except Exception:
            scale = 1.0
        S = lambda v: int(round(v * scale))  # noqa: E731
        W, H = S(168), S(40)
        KEY = "#010203"
        BG = "#151517"

        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=KEY)
        root.attributes("-transparentcolor", KEY)
        root.attributes("-alpha", 0.0)

        rect = wintypes.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
        x = (rect.left + rect.right - W) // 2
        y = rect.bottom - H - S(28)
        root.geometry(f"{W}x{H}+{x}+{y}")
        c = tk.Canvas(root, width=W, height=H, bg=KEY, highlightthickness=0, bd=0)
        c.pack()
        root.update_idletasks()

        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        GWL_EXSTYLE = -20
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        # NOACTIVATE | TOOLWINDOW (no taskbar) | TRANSPARENT (click-through) | LAYERED
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | 0x08000000 | 0x80 | 0x20 | 0x80000)

        bars = 14
        hist = deque([0.0] * bars, maxlen=bars)
        alpha = [0.0]
        t = [0]

        def pill():
            r = H // 2
            c.create_oval(0, 0, H, H, fill=BG, outline=BG)
            c.create_oval(W - H, 0, W, H, fill=BG, outline=BG)
            c.create_rectangle(r, 0, W - r, H, fill=BG, outline=BG)

        def tick():
            t[0] += 1
            st = self.state
            target = 0.0 if st == "idle" else 0.96
            alpha[0] += (target - alpha[0]) * 0.35
            if abs(alpha[0] - target) < 0.02:
                alpha[0] = target
            root.attributes("-alpha", alpha[0])
            if alpha[0] > 0:
                c.delete("all")
                pill()
                if st == "rec":
                    lvl = min(1.0, max(0.0, self.level_fn() * 9))
                    hist.append(lvl)
                    pr = S(4) + S(1.2) * (1 + math.sin(t[0] / 3))
                    cx, cy = S(22), H // 2
                    c.create_oval(cx - pr, cy - pr, cx + pr, cy + pr, fill="#e5484d", outline="")
                    x0, gap, bw = S(40), S(8), S(3)
                    for i, v in enumerate(hist):
                        h = max(S(3), v * (H - S(16)))
                        bx = x0 + i * gap
                        c.create_rectangle(bx, cy - h / 2, bx + bw, cy + h / 2,
                                           fill="#f2f2f3", outline="")
                elif st == "busy":
                    cy = H // 2
                    c.create_text(S(18), cy, text=self.busy_text, fill="#d8d8db", anchor="w",
                                  font=("Segoe UI", -S(13)))
                    for i in range(3):
                        ph = math.sin(t[0] / 3 - i * 0.9)
                        rr = S(2.6)
                        dx = S(126) + i * S(11)
                        dy = cy - ph * S(3)
                        c.create_oval(dx - rr, dy - rr, dx + rr, dy + rr,
                                      fill="#f2f2f3" if ph > 0 else "#77777d", outline="")
            root.after(33, tick)

        tick()
        root.mainloop()
