"""Text of a YouTube video from its subtitles (no video or audio download)."""
import json
import logging
import re

log = logging.getLogger("voicetype")

TAG = re.compile(r"[♪♫♬]+|\[[^\]]{1,40}\]|\([^)]{0,3}(музыка|music|смех|laughter|аплодисменты|applause)[^)]{0,3}\)", re.I)


class YTError(Exception):
    """Error with a message for the user (code + details)."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def video_id(url: str):
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([\w-]{11})", url)
    if m:
        return m.group(1)
    return url if re.fullmatch(r"[\w-]{11}", url.strip()) else None


# ---------------------------------------------------------------- choosing a track
def _match(keys, lang):
    """Keys of a subtitles dict that are this language: 'ru', 'ru-RU', 'en-US'..."""
    return [k for k in keys if k == lang or k.split("-")[0] == lang and not k.endswith("-orig")]


def _is_translation(formats) -> bool:
    """YouTube marks machine-translated caption tracks with a 'tlang' URL parameter."""
    return any("tlang=" in (f.get("url") or "") for f in formats)


def pick_track(info: dict, pref: str):
    """-> (formats, lang, kind) where kind is 'manual' | 'auto' | 'translated'."""
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat" and v}
    auto = {k: v for k, v in (info.get("automatic_captions") or {}).items() if v}

    # the language actually spoken: the speech-recognition track that is not a translation
    meta = (info.get("language") or "").split("-")[0]
    asr = [k for k in auto if k.endswith("-orig")]
    if not asr:
        plain = [k for k, v in auto.items() if not _is_translation(v)]
        # trust "not a translation" only when it singles out one track; otherwise use metadata
        if len(plain) == 1:
            asr = plain
        elif meta and _match(auto, meta):
            asr = [_match(auto, meta)[0]]
    spoken = asr[0].replace("-orig", "").split("-")[0] if asr else ""
    orig = spoken or meta

    def from_manual(lang):
        ks = _match(manual, lang)
        return (manual[ks[0]], ks[0], "manual") if ks else None

    def from_asr(lang=None):
        ks = [k for k in asr if lang is None or k.replace("-orig", "").split("-")[0] == lang]
        return (auto[ks[0]], ks[0].replace("-orig", ""), "auto") if ks else None

    def from_auto(lang):
        ks = _match(auto, lang)
        if not ks:
            return None
        v = auto[ks[0]]
        return v, ks[0], "translated" if _is_translation(v) else "auto"

    if pref in ("ru", "en"):
        order = [lambda: from_manual(pref), lambda: from_asr(pref), lambda: from_auto(pref)]
    else:
        order = [lambda: from_manual(orig) if orig else None,
                 lambda: from_asr(),
                 lambda: from_manual(meta) if meta else None,
                 lambda: from_auto(orig) if orig else None,
                 lambda: (next(iter(manual.values())), next(iter(manual)), "manual") if manual else None]
    for f in order:
        r = f()
        if r:
            return r
    return None


# ---------------------------------------------------------------- parsing
def parse_json3(data: str):
    """YouTube json3 subtitles -> [{t, end, text}]."""
    j = json.loads(data)
    out = []
    for ev in j.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        txt = "".join(s.get("utf8", "") for s in segs).replace("\n", " ")
        txt = re.sub(r"\s+", " ", TAG.sub(" ", txt)).strip()
        if not txt:
            continue
        st = ev.get("tStartMs", 0) / 1000
        out.append({"t": st, "end": st + ev.get("dDurationMs", 0) / 1000, "text": txt})
    return out


def paragraphs(cues, mode="long", every=60.0):
    """Join subtitle cues into readable paragraphs (~1 min each by default)."""
    if mode == "chunk":
        every = 20.0
    paras = []
    for c in cues:
        if paras and (mode == "none" or c["t"] - paras[-1]["t"] < every):
            p = paras[-1]
            p["text"] += " " + c["text"]
            p["end"] = c["end"]
        else:
            paras.append(dict(c))
    for p in paras:
        p["text"] = re.sub(r"\s+", " ", p["text"]).strip()
        p["text"] = re.sub(r"\s+([,.!?…])", r"\1", p["text"])
        if p["text"]:
            p["text"] = p["text"][0].upper() + p["text"][1:]
    return paras


# ---------------------------------------------------------------- fetching
def _via_ytdlp(url, pref):
    import yt_dlp
    opts = {"skip_download": True, "quiet": True, "no_warnings": True, "noplaylist": True,
            "socket_timeout": 20}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        picked = pick_track(info, pref)
        if not picked:
            raise YTError("nosubs", info.get("title", ""))
        formats, lang, kind = picked
        fmt = next((f for f in formats if f.get("ext") == "json3"), None)
        if not fmt:
            raise YTError("format", ", ".join(f.get("ext", "?") for f in formats))
        data = ydl.urlopen(fmt["url"]).read().decode("utf-8")
    cues = parse_json3(data)
    if not cues:
        raise YTError("nosubs", info.get("title", ""))
    return {"title": info.get("title") or video_id(url), "channel": info.get("channel") or info.get("uploader") or "",
            "duration": info.get("duration") or (cues[-1]["end"] if cues else 0),
            "url": info.get("webpage_url") or url, "lang": lang, "kind": kind, "cues": cues}


def _via_transcript_api(url, pref):
    from youtube_transcript_api import YouTubeTranscriptApi
    vid = video_id(url)
    if not vid:
        raise YTError("badurl", url)
    api = YouTubeTranscriptApi()
    tl = list(api.list(vid))
    if not tl:
        raise YTError("nosubs", vid)
    manual = [t for t in tl if not t.is_generated]
    auto = [t for t in tl if t.is_generated]
    want = [pref] if pref in ("ru", "en") else []
    tr = None
    for group in ((manual, auto) if not want else (manual, auto)):
        for t in group:
            if not want or t.language_code.split("-")[0] in want:
                tr = t
                break
        if tr:
            break
    kind = "auto" if tr and tr.is_generated else "manual"
    if tr is None:  # translate the original into the wanted language
        base = (auto or manual)[0]
        tr = base.translate(pref) if base.is_translatable else base
        kind = "translated"
    fetched = tr.fetch()
    cues = []
    for sn in fetched:
        txt = re.sub(r"\s+", " ", TAG.sub(" ", sn.text.replace("\n", " "))).strip()
        if txt:
            cues.append({"t": sn.start, "end": sn.start + sn.duration, "text": txt})
    if not cues:
        raise YTError("nosubs", vid)
    return {"title": vid, "channel": "", "duration": cues[-1]["end"], "url": f"https://www.youtube.com/watch?v={vid}",
            "lang": tr.language_code, "kind": kind, "cues": cues}


def fetch(url: str, pref: str = "orig") -> dict:
    url = url.strip()
    if not video_id(url) and "youtu" not in url:
        raise YTError("badurl", url)
    errors = []
    for fn in (_via_ytdlp, _via_transcript_api):
        try:
            return fn(url, pref)
        except YTError as e:
            if e.code in ("nosubs", "badurl"):
                errors.append(e)
                continue
            errors.append(e)
        except Exception as e:  # network, YouTube changes, blocked region...
            log.exception("youtube via %s", fn.__name__)
            errors.append(YTError("network", str(e)[:300]))
    for e in errors:
        if e.code == "nosubs":
            raise e
    raise errors[-1] if errors else YTError("network", "")
