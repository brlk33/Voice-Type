"""Windows clipboard for pasting dictation without leaving traces.

- The dictated text is put on the clipboard with the formats Windows and clipboard managers
  understand as «don't record this»: it doesn't show up in Win+V history or the cloud clipboard.
- Whatever was on the clipboard before (text, image, files...) is saved with all its formats
  and put back afterwards, also without creating a new history entry.
"""
import ctypes
import logging
import time
from ctypes import wintypes

log = logging.getLogger("voicetype")

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
# formats whose data are GDI/other handles, not HGLOBAL memory: can't be copied byte-wise
# (Windows re-synthesizes them from CF_DIB / CF_UNICODETEXT etc.)
_SKIP = {2, 3, 9, 14, 0x0082, 0x008E, 0x0083}  # BITMAP, METAFILEPICT, PALETTE, ENHMETAFILE, DSP*

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32.OpenClipboard.argtypes = [wintypes.HWND]
u32.OpenClipboard.restype = wintypes.BOOL
u32.CloseClipboard.restype = wintypes.BOOL
u32.EmptyClipboard.restype = wintypes.BOOL
u32.EnumClipboardFormats.argtypes = [wintypes.UINT]
u32.EnumClipboardFormats.restype = wintypes.UINT
u32.GetClipboardData.argtypes = [wintypes.UINT]
u32.GetClipboardData.restype = wintypes.HANDLE
u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
u32.SetClipboardData.restype = wintypes.HANDLE
u32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
u32.RegisterClipboardFormatW.restype = wintypes.UINT
k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
k32.GlobalAlloc.restype = wintypes.HGLOBAL
k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
k32.GlobalLock.restype = ctypes.c_void_p
k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
k32.GlobalSize.argtypes = [wintypes.HGLOBAL]
k32.GlobalSize.restype = ctypes.c_size_t
k32.GlobalFree.argtypes = [wintypes.HGLOBAL]

_NO_HISTORY = [
    (u32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing"), b"\0\0\0\0"),
    (u32.RegisterClipboardFormatW("CanIncludeInClipboardHistory"), b"\0\0\0\0"),
    (u32.RegisterClipboardFormatW("CanUploadToCloudClipboard"), b"\0\0\0\0"),
]


_MARKS = {f for f, _ in _NO_HISTORY}


def _open():
    for _ in range(20):  # another app may hold it for a moment
        if u32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def _hglobal(data: bytes):
    h = k32.GlobalAlloc(GMEM_MOVEABLE, max(1, len(data)))
    if not h:
        return None
    p = k32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    k32.GlobalUnlock(h)
    return h


def _put(items):
    """items: [(format, bytes)]. Clipboard must be open and emptied."""
    for fmt, data in items:
        h = _hglobal(data)
        if h and not u32.SetClipboardData(fmt, h):
            k32.GlobalFree(h)


def save():
    """Everything on the clipboard now, as [(format, bytes)]."""
    if not _open():
        return None
    try:
        out, fmt = [], 0
        while True:
            fmt = u32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if fmt in _SKIP or fmt in _MARKS or 0x0200 <= fmt <= 0x03FF:  # private / GDI handles
                continue
            h = u32.GetClipboardData(fmt)
            if not h:
                continue
            p = k32.GlobalLock(h)
            if not p:
                continue
            try:
                out.append((fmt, ctypes.string_at(p, k32.GlobalSize(h))))
            finally:
                k32.GlobalUnlock(h)
        return out
    finally:
        u32.CloseClipboard()


def set_private_text(text: str) -> bool:
    if not _open():
        return False
    try:
        u32.EmptyClipboard()
        _put([(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))] + _NO_HISTORY)
        return True
    finally:
        u32.CloseClipboard()


def restore(items) -> None:
    if items is None or not _open():
        return
    try:
        u32.EmptyClipboard()
        # the old content is already in the history; putting it back must not add it again
        _put(list(items) + (_NO_HISTORY if items else []))
    finally:
        u32.CloseClipboard()
