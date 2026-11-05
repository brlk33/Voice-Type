"""«Мой стиль»: learn how the user writes in chats and format dictation the same way.

Import: Telegram export (result.json or messages*.html), WhatsApp export (.txt) or plain text
(one message per line). Only the user's own messages are analysed, locally.

The profile (style_profile.json) keeps numbers and ~30 example messages, not the whole chat.
Simple habits (case, periods, commas, "?", "ё") are applied by rules; with the editor model
installed, the examples also let it copy CAPS emphasis and the user's typical spellings.
"""
import html
import json
import logging
import random
import re
from collections import Counter
from pathlib import Path

import core

log = logging.getLogger("voicetype")
PROFILE = core.ROOT / "style_profile.json"
MAX_SAMPLES = 30

_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*")
_URL = re.compile(r"https?://\S+|www\.\S+|t\.me/\S+")
_QWORDS = re.compile(r"^(как|что|чё|че|где|когда|почему|зачем|куда|откуда|сколько|кто|какой|какая|какие|какое|чей|можно|можешь|сможешь|будешь|есть|правда|ты|вы)\b", re.I)
_CONJ = ("что", "чтобы", "но", "если", "когда", "который", "которая", "которые", "которое", "потому")
_CAPS = re.compile(r"\b[A-ZА-ЯЁ]{3,}\b")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


# ================================================================ reading exports
def _tg_text(t):
    if isinstance(t, str):
        return t
    if isinstance(t, list):
        return "".join(x if isinstance(x, str) else str(x.get("text", "")) for x in t)
    return ""


def _from_tg_json(data):
    """-> (messages [(sender, text)], self_name or None)."""
    me_id = None
    pi = data.get("personal_information") or {}
    if pi.get("user_id"):
        me_id = f"user{pi['user_id']}"
    chats = data.get("chats", {}).get("list") if isinstance(data.get("chats"), dict) else None
    chats = chats if chats is not None else [data]
    out, me_name = [], None
    for chat in chats:
        for m in chat.get("messages", []):
            if m.get("type") != "message" or m.get("forwarded_from"):
                continue
            txt = _tg_text(m.get("text"))
            sender = m.get("from") or m.get("actor") or "?"
            if me_id and m.get("from_id") == me_id:
                me_name = sender
            if txt.strip():
                out.append((sender, txt))
    return out, me_name


def _from_tg_html(src):
    out, sender = [], None
    for chunk in src.split('<div class="message ')[1:]:
        head = chunk[:40]
        if head.startswith("service"):
            continue
        if 'class="forwarded body"' in chunk:
            m = re.search(r'<div class="from_name">\s*(.*?)\s*</div>', chunk.split('class="forwarded body"')[0], re.S)
            if m:
                sender = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
            continue
        m = re.search(r'<div class="from_name">\s*(.*?)\s*</div>', chunk, re.S)
        if m:
            sender = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        t = re.search(r'<div class="text">(.*?)</div>', chunk, re.S)
        if t and sender:
            txt = re.sub(r"<br\s*/?>", "\n", t.group(1))
            txt = html.unescape(re.sub(r"<[^>]+>", "", txt)).strip()
            if txt:
                out.append((sender, txt))
    return out


_WA = re.compile(r"^\[?(\d{1,2}[./]\d{1,2}[./]\d{2,4}),?\s+(\d{1,2}:\d{2})(?::\d{2})?(?:\s?[AP]M)?\]?\s*[-–]?\s*([^:]{1,40}):\s(.*)$")


def _from_text(src):
    """WhatsApp export, or plain text: one message per line (sender unknown)."""
    lines = src.splitlines()
    wa = sum(1 for ln in lines[:200] if _WA.match(ln))
    if wa >= 3:
        out = []
        for ln in lines:
            m = _WA.match(ln)
            if m:
                out.append([m.group(3).strip(), m.group(4)])
            elif out and ln.strip():
                out[-1][1] += "\n" + ln
        return [(s, t) for s, t in out if "<" not in t[:2]]  # skip "<Media omitted>"
    return [(None, ln.strip()) for ln in lines if ln.strip()]


def read_files(paths):
    """-> {"messages": [(sender, text)], "senders": [(name, count)], "me": name or None}"""
    msgs, me = [], None
    for p in paths:
        p = Path(p)
        raw = p.read_text(encoding="utf-8", errors="replace")
        if p.suffix.lower() == ".json":
            got, name = _from_tg_json(json.loads(raw))
            me = me or name
        elif p.suffix.lower() in (".html", ".htm"):
            got = _from_tg_html(raw)
        else:
            got = _from_text(raw)
        log.info("Мой стиль: %s — %d сообщений", p.name, len(got))
        msgs += got
    senders = Counter(s for s, _ in msgs if s)
    return {"messages": msgs, "senders": senders.most_common(), "me": me}


# ================================================================ analysis
def _usable(t):
    t = _URL.sub("", t).strip()
    return t if len(_WORD.findall(t)) >= 2 else ""


def build(texts):
    """Profile from the user's own messages."""
    texts = [u for u in (_usable(t) for t in texts) if u]
    n = len(texts)
    if n < 10:
        raise ValueError("few")
    st = Counter()
    words_total = 0
    for t in texts:
        words = _WORD.findall(t)
        words_total += len(words)
        first = next((c for c in t if c.isalpha()), "")
        if first:
            st["start_letter"] += 1
            st["start_lower"] += first.islower()
        # sentence starts inside the message
        for m in re.finditer(r"[.!?…]\s+([A-Za-zА-Яа-яЁё])", t):
            st["inner_starts"] += 1
            st["inner_lower"] += m.group(1).islower()
        end = t.rstrip()[-1:]
        st["end_period"] += end == "." and not t.rstrip().endswith("..")
        st["end_paren"] += end == ")" or end == "("
        st["end_bang"] += end == "!"
        st["ellipsis"] += "..." in t or "…" in t
        if _QWORDS.match(t.lower()):
            st["questions"] += 1
            st["questions_marked"] += "?" in t
        if len(words) >= 10:
            st["long"] += 1
            st["long_inner_dot"] += bool(re.search(r"[.!?…]\s+\S", t))
        for c in _CONJ:
            for m in re.finditer(rf"(\S)\s+{c}\b", t, re.I):
                st["conj"] += 1
                st["conj_comma"] += m.group(1).endswith(",")
        caps = [w for w in _CAPS.findall(t) if not w.isascii() or len(w) > 4]
        st["caps_msgs"] += bool(caps) and t.upper() != t
        st["emoji"] += bool(_EMOJI.search(t))
        st["yo"] += t.count("ё") + t.count("Ё")
        st["newline"] += "\n" in t.strip()
    r = lambda a, b: round(st[a] / st[b], 3) if st[b] else None  # noqa: E731
    stats = {
        "messages": n, "words": words_total,
        "avg_words": round(words_total / n, 1),
        "start_lower": r("start_lower", "start_letter"),
        "inner_lower": r("inner_lower", "inner_starts"),
        "end_period": round(st["end_period"] / n, 3),
        "end_paren": round(st["end_paren"] / n, 3),
        "end_bang": round(st["end_bang"] / n, 3),
        "ellipsis": round(st["ellipsis"] / n, 3),
        "question_mark": r("questions_marked", "questions"),
        "inner_dots": r("long_inner_dot", "long"),
        "comma_conj": r("conj_comma", "conj"),
        "caps": round(st["caps_msgs"] / n, 3),
        "emoji": round(st["emoji"] / n, 3),
        "yo": st["yo"],
        "newlines": round(st["newline"] / n, 3),
    }
    # examples for the editor: normal-length messages, spread over the whole history
    pool = list(dict.fromkeys(t for t in texts if 4 <= len(_WORD.findall(t)) <= 40 and len(t) <= 280))
    rnd = random.Random(7)
    samples = rnd.sample(pool, min(MAX_SAMPLES, len(pool)))
    # make sure CAPS / smiley habits are visible in the examples if the user has them
    for key, test in (("caps", lambda t: bool(_CAPS.search(t))), ("end_paren", lambda t: t.rstrip().endswith((")", "(")))):
        if stats[key] >= 0.03 and not any(test(s) for s in samples):
            extra = [t for t in pool if test(t)][:3]
            samples = extra + samples[:MAX_SAMPLES - len(extra)]
    return {"version": 1, "stats": stats, "samples": samples}


def rules(prof):
    """Decide which habits to enforce. Returns dict of flags (shown in the UI too)."""
    s = prof["stats"]
    v = lambda k, d=0.5: d if s.get(k) is None else s[k]  # noqa: E731
    commas = v("comma_conj", 1.0)
    return {
        "lower_start": v("start_lower", 0) >= 0.6,
        "lower_inner": v("inner_lower", v("start_lower", 0)) >= 0.6,
        "no_final_period": v("end_period", 1) <= 0.2,
        "no_inner_dots": v("inner_dots", 1) <= 0.25,
        "commas": "none" if commas < 0.2 else "few" if commas < 0.5 else "normal",
        "no_question_mark": v("question_mark", 1) < 0.3,
        "no_yo": s.get("yo", 1) == 0 and s.get("words", 0) >= 300,
        "caps": v("caps", 0) >= 0.03,
        "parens": v("end_paren", 0) >= 0.08,
    }


def describe(prof, lang="ru"):
    """Human-readable list of detected habits for the settings card."""
    f, s = rules(prof), prof["stats"]
    pct = lambda x: f"{round((x or 0) * 100)}%"  # noqa: E731
    ru = lang == "ru"
    out = []
    out.append((ru and "Начинаете с маленькой буквы" or "Start with a lowercase letter", pct(s["start_lower"]), f["lower_start"]))
    out.append((ru and "Точка в конце сообщения" or "Period at the end", pct(s["end_period"]), not f["no_final_period"]))
    cm = {"none": ru and "почти не ставите" or "almost never", "few": ru and "редко" or "rarely", "normal": ru and "как по правилам" or "as in the rules"}[f["commas"]]
    out.append((ru and "Запятые перед «что», «но»" or "Commas before conjunctions", cm, f["commas"] == "normal"))
    if s.get("question_mark") is not None:
        out.append((ru and "Вопросительный знак в вопросах" or "Question mark in questions", pct(s["question_mark"]), not f["no_question_mark"]))
    out.append((ru and "Слова КАПСОМ для выделения" or "CAPS for emphasis", pct(s["caps"]), f["caps"]))
    out.append((ru and "Скобка-смайл в конце )" or "Smiley bracket at the end )", pct(s["end_paren"]), f["parens"]))
    out.append((ru and "Буква «ё»" or "Letter «ё»", (ru and "не используете" or "never") if f["no_yo"] else (ru and "используете" or "used"), not f["no_yo"]))
    return [{"label": a, "value": b, "on": c} for a, b, c in out]


# ================================================================ applying
def apply(text, prof, protect=()):
    """Rule-based formatting in the user's manner (runs after the editor, if any)."""
    if not text or not prof:
        return text
    f = rules(prof)
    protect = set(protect)
    lw = lambda m: core._lower_word(m.group(1), protect)  # noqa: E731
    if f["lower_start"]:
        text = re.sub(r"^(\S+)", lw, text)
    if f["lower_inner"]:
        text = re.sub(r"(?<=[.!?…]\s)(\S+)", lw, text)
    if f["commas"] == "none":
        text = re.sub(r",(?=\s)", "", text)
    elif f["commas"] == "few":
        text = re.sub(r",(?=\s+(?:что|чтобы|если|когда|который|которая|которые|которое|как|потому)\b)", "", text, flags=re.I)
    if f["no_inner_dots"]:
        # sentences glued with a comma (or nothing); the next word loses its capital
        sep = "" if f["commas"] == "none" else ","
        text = re.sub(r"(?<!\.)\.(?!\.)\s+(\S+)", lambda m: sep + " " + core._lower_word(m.group(1), protect), text)
    if f["no_question_mark"]:
        text = re.sub(r"\?\s*$", "", text.rstrip())
        text = re.sub(r"\?\s+(\S+)", lambda m: " " + core._lower_word(m.group(1), protect), text)
    if f["no_final_period"]:
        text = re.sub(r"(?<!\.)\.\s*$", "", text.rstrip())
    if f["no_yo"]:
        text = text.replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


# ================================================================ editor prompt
def editor_prompt(prof):
    """Extra instructions + examples for the editor model."""
    f = rules(prof)
    habits = []
    habits.append("начинает сообщения с маленькой буквы" if f["lower_start"] else "начинает предложения с заглавной буквы")
    habits.append("не ставит точку в конце" if f["no_final_period"] else "ставит точку в конце")
    habits.append({"none": "почти не ставит запятых", "few": "ставит запятые редко", "normal": "ставит запятые по правилам"}[f["commas"]])
    if f["caps"]:
        habits.append("иногда выделяет одно эмоциональное слово КАПСОМ")
    if f["parens"]:
        habits.append("иногда ставит в конце скобку-смайл «)»")
    if f["no_yo"]:
        habits.append("пишет «е» вместо «ё»")
    text = ("\n\nОформи текст так, как этот человек пишет в мессенджерах: " + "; ".join(habits) + ". "
            "Копируй его манеру: регистр, пунктуацию, выделение КАПСОМ, его привычные сокращённые написания слов (если такое же слово встречается в примерах). "
            "Смысл, порядок слов и сами слова не меняй, новых слов не добавляй. "
            "Названия программ и слова из словаря пиши как в словаре, даже если в примерах они написаны иначе.\n\nПримеры его сообщений:\n"
            + "\n".join("— " + s.replace("\n", " ") for s in prof["samples"][:MAX_SAMPLES]))
    return text


# ================================================================ storage
def load():
    try:
        return json.loads(PROFILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(prof):
    PROFILE.write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")


def remove():
    try:
        PROFILE.unlink()
    except OSError:
        pass
