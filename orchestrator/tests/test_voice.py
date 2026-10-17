"""Voice assistant (Sarvam): understanding, grounded answers, Hindi round trip, read-only, failures. Sarvam is simulated."""
import json
import os
import tempfile
import time

os.environ.setdefault("OEA_DB", os.path.join(tempfile.mkdtemp(), "orch_test.db"))
os.environ.setdefault("OEA_DISABLE_LOOP", "1")
os.environ.setdefault("OEA_AGENT_MODE", "rules")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent.tools import Services  # noqa: E402
from orchestrator import voice_api  # noqa: E402
from orchestrator.app import State, app as orch_app  # noqa: E402
from services.app import app as services_app  # noqa: E402

services_http = TestClient(services_app)
State.services_factory = lambda: Services(services_http)
api = TestClient(orch_app)
KEY = "sk_test_sarvam"


class FakeSarvam:
    """Checks requests the way the Sarvam REST API expects them and returns scripted answers."""

    def __init__(self, transcript="", language="en-IN", fail=None):
        self.transcript, self.language, self.fail = transcript, language, fail
        self.calls = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["api-subscription-key"] == KEY
        path = req.url.path
        self.calls.append(path)
        if self.fail == path:
            return httpx.Response(500, json={"error": "boom"})
        if path == "/speech-to-text":
            body = req.read()
            assert b'name="file"' in body and b'name="model"' in body and b"saaras:v4" in body
            assert b'name="language_code"' not in body                        # auto-detect
            return httpx.Response(200, json={"request_id": "r1", "transcript": self.transcript,
                                             "language_code": self.language, "language_probability": 0.9})
        if path == "/translate":
            b = json.loads(req.content)
            assert {"input", "source_language_code", "target_language_code"} <= set(b)
            if b["target_language_code"] == "en-IN":
                return httpx.Response(200, json={"translated_text": "Explain order 1002", "source_language_code": "hi-IN"})
            return httpx.Response(200, json={"translated_text": "[hi] " + b["input"], "source_language_code": "en-IN"})
        if path == "/text-to-speech":
            b = json.loads(req.content)
            assert b["text"] and b["language_code"] in ("en-IN", "hi-IN") and b["model"]
            self.tts_language = b["language_code"]
            return httpx.Response(200, json={"request_id": "r2", "audios": ["UklGRiQAAABXQVZF"]})
        return httpx.Response(404)


@pytest.fixture
def sarvam(monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", KEY)
    fake = FakeSarvam()
    monkeypatch.setattr(voice_api, "transport", httpx.MockTransport(fake))
    api.post("/reset")
    return fake


def ask(body=b"\x1aE\xdf\xa3fake-webm", ctype="audio/webm;codecs=opus"):
    return api.post("/voice/ask", content=body, headers={"Content-Type": ctype})


def wait_waiting(eid):
    for _ in range(200):
        e = api.get(f"/exceptions/{eid}").json()
        if e["status"] == "awaiting_approval" and not e["investigating"]:
            return e
        time.sleep(0.05)
    raise AssertionError("not awaiting")


def test_without_key_uses_browser_voice(monkeypatch):
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    assert api.get("/voice/config").json() == {"enabled": True, "engine": "browser", "languages": ["en-IN"]}
    assert ask().status_code == 409                                       # Sarvam route stays off
    monkeypatch.setenv("SARVAM_API_KEY", KEY)
    assert api.get("/voice/config").json()["engine"] == "sarvam"
    monkeypatch.setenv("OEA_VOICE_ENGINE", "browser")                     # force browser voice even with a key
    assert api.get("/voice/config").json()["engine"] == "browser"


def test_text_question_answers_from_live_cases(monkeypatch):
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    api.post("/reset")
    eid = api.post("/trigger?fail=sc2").json()["exceptions_created"][0]
    wait_waiting(eid)
    r = api.post("/voice/text", json={"text": "What needs my attention?"}).json()
    assert r["intent"] == "attention" and "ORD-1002" in r["answer"] and r["action"]["path"] == "approvals"
    r = api.post("/voice/text", json={"text": "approve order 1002"}).json()
    assert r["intent"] == "approve" and r["action"]["auto"] is True and r["action"]["path"] == f"case/{eid}"
    assert api.get(f"/exceptions/{eid}").json()["status"] == "awaiting_approval"   # read-only
    assert api.post("/voice/text", json={"text": "  "}).status_code == 422
    assert api.post("/voice/text", content=b"not json", headers={"Content-Type": "application/json"}).status_code == 422


def test_understands_order_numbers():
    f = voice_api.find_order
    assert f("explain ORD-1002") == f("order 1002 please") == f("one zero zero two") == f("10 02") == "ORD-1002"
    assert f("what needs my attention") is None
    assert voice_api.intent_of("approve order 1004") == ("approve", "ORD-1004")
    assert voice_api.intent_of("What needs my attention?") == ("attention", None)
    assert voice_api.intent_of("how are we doing today")[0] == "impact"
    assert voice_api.intent_of("tell me a joke")[0] == "help"


def test_attention_answer_comes_from_live_cases(sarvam):
    eid = api.post("/trigger?fail=sc2").json()["exceptions_created"][0]
    wait_waiting(eid)
    sarvam.transcript = "What needs my attention?"
    r = ask().json()
    assert r["intent"] == "attention" and r["language"] == "en-IN"
    assert "ORD-1002" in r["answer"] and "1 case needs your decision" in r["answer"]
    assert r["action"]["path"] == "approvals" and r["audio"]
    assert KEY not in json.dumps(r)                                       # the key never leaves the backend
    assert sarvam.calls == ["/speech-to-text", "/text-to-speech"]          # English: no translation calls


def test_hindi_round_trip_and_approve_is_read_only(sarvam):
    eid = api.post("/trigger?fail=sc2").json()["exceptions_created"][0]
    wait_waiting(eid)
    sarvam.transcript, sarvam.language = "ऑर्डर 1002 के बारे में बताओ", "hi-IN"
    r = ask().json()
    assert r["language"] == "hi-IN" and r["heard_english"] == "Explain order 1002"
    assert r["answer"].startswith("[hi] ORD-1002") and "Recommended" in r["answer_english"]
    assert sarvam.tts_language == "hi-IN"
    assert sarvam.calls == ["/speech-to-text", "/translate", "/translate", "/text-to-speech"]

    sarvam.calls.clear()
    sarvam.transcript, sarvam.language = "approve order 1002", "en-IN"
    r = ask().json()
    assert r["intent"] == "approve" and r["action"] == {"type": "open", "path": f"case/{eid}", "label": "Open ORD-1002", "auto": True}
    assert "press Approve to confirm" in r["answer"]
    e = api.get(f"/exceptions/{eid}").json()
    assert e["status"] == "awaiting_approval" and all(a["status"] != "executed" for a in e["actions"])   # nothing ran


def test_failures_are_clear(sarvam):
    sarvam.fail = "/speech-to-text"
    r = ask()
    assert r.status_code == 502 and "Sarvam" in r.json()["detail"]
    sarvam.fail, sarvam.transcript = "/text-to-speech", "how are we doing"
    r = ask().json()
    assert r["intent"] == "impact" and r["answer"] and r["audio"] is None   # still answers in text
    sarvam.fail, sarvam.transcript = None, ""
    assert "didn't catch" in ask().json()["answer"]
    assert ask(body=b"").status_code == 422


def test_conversation_follow_ups(monkeypatch):
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    api.post("/reset")
    e2 = api.post("/trigger?fail=sc2").json()["exceptions_created"][0]
    e4 = api.post("/trigger?fail=sc4").json()["exceptions_created"][0]
    wait_waiting(e2); wait_waiting(e4)
    say = lambda text, ctx=None: api.post("/voice/text", json={"text": text, "context": ctx}).json()

    r = say("What needs my attention?")
    assert r["follow_up"].startswith("Would you like me to explain ORD-100") and r["context"]["suggest"] == "explain"
    first = r["context"]["order"]
    r = say("yes please", r["context"])                                    # "yes" = the suggestion
    assert r["intent"] == "explain" and r["order"] == first and first in r["answer"]
    assert r["follow_up"] == "Shall I open it so you can approve the fix?"
    r = say("why", r["context"])                                           # "why" = the reasons for the same order
    assert r["intent"] == "why" and r["order"] == first and "needs a person because" in r["answer"]
    r = say("tell me more about it", r["context"])                         # "it" = the same order
    assert r["intent"] == "explain" and r["order"] == first
    r = say("approve it", r["context"])
    assert r["intent"] == "approve" and r["order"] == first and r["action"]["auto"] is True
    other = ({"ORD-1002", "ORD-1004"} - {first}).pop()
    assert other in r["follow_up"]                                         # offers the other waiting case
    r = say("next one", r["context"])
    assert r["intent"] == "explain" and r["order"] == other
    r = say("no thanks", r["context"])
    assert r["end"] is True and r["follow_up"] is None
    for eid in (e2, e4):                                                   # talking never approved anything
        assert api.get(f"/exceptions/{eid}").json()["status"] == "awaiting_approval"
    r = say("how are we doing")
    assert r["intent"] == "impact" and r["context"] == {"suggest": "attention"}
    assert say("tell me a joke")["follow_up"] == "What would you like to know?"
