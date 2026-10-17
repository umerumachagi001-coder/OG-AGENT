"""Voice assistant for the ops team, powered by Sarvam AI (speech in, speech out, English + Hindi).

    GET  /voice/config   which voice engine to use: "sarvam" (key set) or "browser" (no key, English)
    POST /voice/text     body {"text", "context"} = what the browser heard → answer + a follow-up question (no key)
    POST /voice/ask      body = a short recording from the browser (WebM/WAV/…, ≤ 30 s, Content-Type = its type)
                         → {"heard", "language", "intent", "answer", "audio" (base64 WAV or null), "action"}

Pipeline: Sarvam speech-to-text (language auto-detected) → if Hindi, translate to English to understand it →
answer from the live case data → if Hindi, translate the answer back → Sarvam text-to-speech.

Rules this module keeps:
- Read-only. It answers questions and can *open* a case for approval, but never approves, rejects, undoes,
  triggers or resets anything. A person still confirms every decision on screen.
- Answers come only from the system's own data (cases, policy findings, metrics); nothing is generated freely.
- The Sarvam key stays on the backend (`.env`: SARVAM_API_KEY). Without it the endpoints report "off" and the
  dashboard hides the mic button, so nothing else changes.
- Behind the same Supabase sign-in as the rest of the dashboard API when login is on.
"""
from __future__ import annotations

import base64
import logging
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger("voice")
router = APIRouter(prefix="/voice", tags=["voice"])

MAX_AUDIO_BYTES = 2_000_000          # ~30 s of compressed browser audio is far below this
transport: Any = None                # tests put a simulated Sarvam here (httpx transport)


def settings() -> dict:
    return {
        "key": (os.environ.get("SARVAM_API_KEY") or "").strip().strip("\"'").strip(),   # tolerate quotes in .env
        "url": (os.environ.get("OEA_SARVAM_URL") or "https://api.sarvam.ai").rstrip("/"),
        "stt_model": os.environ.get("OEA_SARVAM_STT_MODEL", "saaras:v4"),
        "tts_model": os.environ.get("OEA_SARVAM_TTS_MODEL", "bulbul:v4-flash"),
        "speaker": (os.environ.get("OEA_SARVAM_SPEAKER") or "").strip(),
        "translate_model": os.environ.get("OEA_SARVAM_TRANSLATE_MODEL", "sarvam-translate:v1"),
    }


class SarvamError(Exception):
    pass


def _client(s: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=s["url"], timeout=30, transport=transport,
                             headers={"api-subscription-key": s["key"]})


async def _speech_to_text(s: dict, audio: bytes, content_type: str) -> dict:
    ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3",
           "audio/mp4": "mp4"}.get(content_type.split(";")[0].strip(), "webm")
    async with _client(s) as c:
        # No language_code: Sarvam detects it and returns language_code (e.g. hi-IN, en-IN).
        r = await c.post("/speech-to-text", files={"file": (f"question.{ext}", audio, content_type)},
                         data={"model": s["stt_model"], "mode": "transcribe"})
    if r.status_code >= 300:
        raise SarvamError(f"speech-to-text answered {r.status_code}: {r.text[:200]}")
    return r.json()


async def _translate(s: dict, text: str, source: str, target: str) -> str:
    async with _client(s) as c:
        r = await c.post("/translate", json={"input": text[:1900], "source_language_code": source,
                                             "target_language_code": target, "model": s["translate_model"]})
    if r.status_code >= 300:
        raise SarvamError(f"translate answered {r.status_code}: {r.text[:200]}")
    return r.json().get("translated_text") or text


async def _text_to_speech(s: dict, text: str, language: str) -> str | None:
    body = {"text": text[:1400], "language_code": language, "model": s["tts_model"]}
    if s["speaker"]:
        body["speaker"] = s["speaker"]
    async with _client(s) as c:
        r = await c.post("/text-to-speech", json=body)
    if r.status_code >= 300:
        raise SarvamError(f"text-to-speech answered {r.status_code}: {r.text[:200]}")
    audios = r.json().get("audios") or []
    return "".join(audios) or None


# ───────── understanding the question (English text) ─────────

_DIGIT_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def find_order(text: str) -> str | None:
    """'ORD-1002', 'order 1002', '1 0 0 2' or 'one zero zero two' → 'ORD-1002'."""
    t = text.lower()
    m = re.search(r"\b(1\d{3})\b", re.sub(r"(?<=\d)[\s-](?=\d)", "", t))
    if m:
        return f"ORD-{m.group(1)}"
    digits = "".join(_DIGIT_WORDS.get(w, " ") for w in re.findall(r"[a-z]+", t)).split()
    for run in digits:
        if len(run) == 4 and run.startswith("1"):
            return f"ORD-{run}"
    return None


def intent_of(text: str) -> tuple[str, str | None]:
    t = text.lower()
    order = find_order(t)
    if order and re.search(r"\bapprov", t):
        return "approve", order
    if order:
        return "explain", order
    if re.search(r"\b(impact|how (are|is) (we|it) doing|performance|metrics?|saved|summary|numbers?)\b", t):
        return "impact", None
    if re.search(r"\b(attention|pending|waiting|need|needs|approv\w*|urgent|queue|open|priority|decision|problems?|cases?)\b", t):
        return "attention", None
    return "help", None


# ───────── answering from live data (English) ─────────

STATUS_WORDS = {"open": "just detected", "investigating": "being investigated", "awaiting_approval": "waiting for your decision",
                "auto_resolved": "fixed automatically", "resolved": "fixed after approval", "rejected": "closed without action",
                "failed": "stopped with an error"}
DOMAIN_WORDS = {"inventory": "stock", "supplier": "supplier", "payment": "payment", "pricing": "pricing", "deadline": "delivery deadline"}


def _short(text: str | None, n: int = 200) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


def _rupees(x: float) -> str:
    return f"{x:,.0f} rupees"


def answer(intent: str, order: str | None) -> tuple[str, dict | None]:
    """English answer + optional dashboard action. Imports the orchestrator lazily (it imports this module)."""
    from orchestrator import app as orch

    live = orch.list_exceptions()
    if intent == "help":
        return ("I can tell you what needs your attention, explain an order such as order one thousand and two, "
                "open an order for approval, or give you today's impact numbers."), None
    if intent == "impact":
        m = orch.metrics()
        if not m["resolved"]:
            return "No cases have been resolved yet.", None
        auto_s = round((m["avg_minutes_auto"] or 0) * 60)
        return (f"{round(m['auto_resolved_pct'])} percent of resolved cases were fixed without a person. "
                f"An automatic fix takes about {auto_s} seconds on average. "
                f"Estimated analyst time saved: {round(m['analyst_minutes_saved'] / 60, 1)} hours."), None
    if intent == "attention":
        waiting = [e for e in live if e["status"] == "awaiting_approval"]
        busy = [e for e in live if e["status"] in ("open", "investigating")]
        if not waiting and not busy:
            return "Nothing needs you right now. Every open problem has been handled.", None
        parts = []
        if waiting:
            names = "; ".join(f"{e['order_id']}, {DOMAIN_WORDS.get(e['domain'], e['domain'])} problem, {e['severity']} severity"
                              for e in waiting[:4])
            parts.append(f"{len(waiting)} {'case needs' if len(waiting) == 1 else 'cases need'} your decision: {names}.")
        if busy:
            parts.append(f"{len(busy)} more {'is' if len(busy) == 1 else 'are'} being investigated.")
        action = {"type": "open", "path": "approvals", "label": "Open approvals"} if waiting else None
        return " ".join(parts), action

    if intent == "why":                                   # follow-up: just the reasons, not the whole case again
        case = next((e for e in sorted(live, key=lambda e: -e["id"]) if e["order_id"] == order), None)
        if not case:
            return f"{order} has no problem on record right now.", None
        e = orch.get_exception(case["id"])
        text = f"The cause: {_short(e.get('root_cause') or e.get('symptom'), 160)}"
        pending = [a for a in e["actions"] if a["status"] in ("proposed", "failed")]
        reasons = ((pending[0]["params"].get("policy") or {}).get("reasons") or []) if pending else []
        if reasons:
            why = "; and ".join(r.replace("_", " ").replace(" > ", " is over ").replace(" < ", " is under ") for r in reasons[:2])
            text += f" It needs a person because {_short(why, 140)}."
        return text, {"type": "open", "path": f"case/{e['id']}", "label": f"Open {e['order_id']}"}

    # explain / approve: the latest case for that order
    case = next((e for e in sorted(live, key=lambda e: -e["id"]) if e["order_id"] == order), None)
    if not case:
        return f"{order} has no problem on record right now.", None
    e = orch.get_exception(case["id"])
    status = STATUS_WORDS.get(e["status"], e["status"])
    text = (f"{e['order_id']}: {DOMAIN_WORDS.get(e['domain'], e['domain'])} problem, {e['severity']} severity, {status}. "
            f"Cause: {_short(e.get('root_cause') or e.get('symptom'), 120)}")
    pending = [a for a in e["actions"] if a["status"] in ("proposed", "failed")]
    done = next((a for a in e["actions"] if a["status"] == "executed"), None)
    if e["status"] == "awaiting_approval" and pending:
        top = pending[0]
        reasons = (top["params"].get("policy") or {}).get("reasons") or []
        text += f" Recommended: {top['params'].get('title')}, {_rupees(top['cost'])}."
        if reasons:
            why = reasons[0].replace("_", " ").replace(" > ", " is over ").replace(" < ", " is under ")
            text += f" It needs a person because {_short(why, 90)}."
    elif done:
        text += f" What was done: {done['params'].get('title')}."
    action = {"type": "open", "path": f"case/{e['id']}", "label": f"Open {e['order_id']}"}
    if intent == "approve":
        if e["status"] == "awaiting_approval" and pending:
            text += " I've opened it for you. Review the option and press Approve to confirm."
            action["auto"] = True
        else:
            text += " There is nothing to approve on it right now."
    return text, action


# ───────── endpoints ─────────

@router.get("/config")
def config():
    """engine "sarvam" when a Sarvam key is set (and OEA_VOICE_ENGINE isn't "browser"), else "browser":
    the browser's own speech recognition + speech (Chrome/Edge, English, no key) talking to POST /voice/text."""
    sarvam = bool(settings()["key"]) and os.environ.get("OEA_VOICE_ENGINE", "").lower() != "browser"
    return {"enabled": True, "engine": "sarvam" if sarvam else "browser",
            "languages": ["en-IN", "hi-IN"] if sarvam else ["en-IN"]}


# ───────── conversation: follow-up questions with a little memory ─────────

_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|ok|okay|please|go ahead|do it|of course|alright)\b")
_NO = re.compile(r"\b(no|nope|nothing|that's all|that is all|thanks|thank you|stop|bye|goodbye|done|not now)\b")
_THAT = re.compile(r"\b(it|that|this|that one|this one|why|cause|reason|more|details?)\b")
_NEXT = re.compile(r"\b(next|another|other one|others?)\b")


def _waiting() -> list[dict]:
    from orchestrator import app as orch
    return [e for e in orch.list_exceptions() if e["status"] == "awaiting_approval"]


def follow_up(intent: str, order: str | None) -> tuple[str, dict]:
    """A short question that keeps the conversation going, plus what "yes" would mean (context)."""
    waiting = _waiting()
    others = [e["order_id"] for e in waiting if e["order_id"] != order]
    if intent == "attention":
        if waiting:
            o = waiting[0]["order_id"]
            return f"Would you like me to explain {o}?", {"suggest": "explain", "order": o}
        return "Would you like today's impact numbers?", {"suggest": "impact"}
    if intent in ("explain", "why"):
        if order in {e["order_id"] for e in waiting}:
            return "Shall I open it so you can approve the fix?", {"suggest": "approve", "order": order}
        if others:
            return f"{others[0]} is also waiting for a decision. Want to hear about it?", {"suggest": "explain", "order": others[0]}
        return "Anything else you'd like to know?", {"order": order}
    if intent == "approve":
        if others:
            return f"After that, {others[0]} is also waiting. Want to hear about it?", {"suggest": "explain", "order": others[0]}
        return "Anything else I can help with?", {"order": order}
    if intent == "impact":
        return "Do you want to hear what needs your attention right now?", {"suggest": "attention"}
    return "What would you like to know?", {}


def converse(text: str, context: dict | None) -> dict:
    """One turn: understands follow-up replies ("yes", "no thanks", "approve it", "next one") using the context
    of the previous turn, answers from live data, and asks a follow-up. Read-only, like everything here."""
    ctx = context if isinstance(context, dict) else {}
    t = text.lower()
    intent, order = intent_of(text)
    if intent in ("help", "attention") and order is None:
        if _NEXT.search(t) and ctx:
            if ctx.get("suggest") == "explain" and ctx.get("order"):      # "next one" = the case just offered
                nxt = [ctx["order"]]
            else:
                nxt = [e["order_id"] for e in _waiting() if e["order_id"] != ctx.get("order")]
            if nxt:
                intent, order = "explain", nxt[0]
            else:
                return {"intent": "none", "order": None, "end": False, "action": None, "context": {"suggest": "impact"},
                        "answer": "There are no other cases waiting for you.", "follow_up": "Would you like today's impact numbers?"}
        elif ctx.get("order") and re.search(r"\bapprov", t):
            intent, order = "approve", ctx["order"]
        elif _NO.search(t) and not re.search(r"\b(attention|pending|waiting|cases?|queue)\b", t):
            return {"intent": "end", "order": None, "answer": "Okay. Press Ask whenever you need me again.",
                    "follow_up": None, "context": {}, "action": None, "end": True}
        elif _YES.search(t) and ctx.get("suggest"):
            intent, order = ctx["suggest"], ctx.get("order")
        elif ctx.get("order") and intent == "help" and re.search(r"\b(why|reason|cause|how come)\b", t):
            intent, order = "why", ctx["order"]
        elif ctx.get("order") and intent == "help" and _THAT.search(t):
            intent, order = "explain", ctx["order"]
    answer_text, action = answer(intent, order)
    question, next_ctx = follow_up(intent, order)
    return {"intent": intent, "order": order, "answer": answer_text, "action": action,
            "follow_up": question, "context": next_ctx, "end": False}


@router.post("/text")
async def ask_text(request: Request):
    """Browser voice (no key): body {"text": "...", "context": {...from the previous answer}} → a grounded,
    read-only answer plus a follow-up question and the context to send with the next turn."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    text = (body.get("text") if isinstance(body, dict) else "") or ""
    text = str(text).strip()[:500]
    if not text:
        raise HTTPException(422, "No question received")
    turn = converse(text, body.get("context"))
    return {"heard": text, "language": "en-IN", **turn}


@router.post("/ask")
async def ask(request: Request):
    s = settings()
    if not s["key"]:
        raise HTTPException(409, "Voice assistant is off: set SARVAM_API_KEY in .env")
    audio = await request.body()
    if not audio:
        raise HTTPException(422, "No audio received")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Recording too long; keep questions under 30 seconds")
    ctype = request.headers.get("content-type", "audio/webm")
    try:
        stt = await _speech_to_text(s, audio, ctype)
    except (SarvamError, httpx.HTTPError) as e:
        log.warning("voice: %s", e)
        raise HTTPException(502, f"Sarvam couldn't process the recording: {e}") from e
    heard = (stt.get("transcript") or "").strip()
    lang = "hi-IN" if (stt.get("language_code") or "").startswith("hi") else "en-IN"

    try:
        english = await _translate(s, heard, "hi-IN", "en-IN") if (heard and lang == "hi-IN") else heard
    except (SarvamError, httpx.HTTPError) as e:
        log.warning("voice translate: %s", e)
        english = heard
    if heard:
        intent, order = intent_of(english)
        text_en, action = answer(intent, order)
    else:
        intent, order, text_en, action = "none", None, "Sorry, I didn't catch that. Please try again.", None

    text = text_en
    if lang == "hi-IN":
        try:
            text = await _translate(s, text_en, "en-IN", "hi-IN")
        except (SarvamError, httpx.HTTPError) as e:
            log.warning("voice translate back: %s", e)
            lang, text = "en-IN", text_en          # fall back to an English answer rather than none
    try:
        audio_b64 = await _text_to_speech(s, text, lang)
    except (SarvamError, httpx.HTTPError) as e:
        log.warning("voice tts: %s", e)
        audio_b64 = None                           # the answer is still shown as text

    return {"heard": heard, "heard_english": english if lang == "hi-IN" else None, "language": lang,
            "intent": intent, "order": order, "answer": text,
            "answer_english": text_en if lang == "hi-IN" else None, "audio": audio_b64, "audio_type": "audio/wav",
            "action": action}
