import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { go } from "../store";
import { Button } from "./ui";

// Voice assistant. Two engines, chosen by the backend (/voice/config):
//  - "browser": the browser's own speech recognition + speech (Chrome/Edge, English, no key) → POST /voice/text.
//    It holds a conversation: each answer ends with a follow-up question, then it listens again by itself.
//  - "sarvam":  record audio → POST /voice/ask (Sarvam AI, English + Hindi). If Sarvam fails, it falls back to "browser".
// A typing box always works as a backup. It answers questions and can open a case, but never approves anything.
const MAX_SECONDS = 25;   // Sarvam's real-time speech-to-text takes up to 30 s
const SR = typeof window !== "undefined" ? window.SpeechRecognition || window.webkitSpeechRecognition : null;
const EXAMPLES = ["What needs my attention?", "Explain order 1002", "Approve order 1002", "How are we doing?"];

function pickMime() {
  const opts = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  return typeof MediaRecorder !== "undefined" ? opts.find((m) => MediaRecorder.isTypeSupported?.(m)) || "" : "";
}

// Speak, then call onDone once (speech end, or a time-based fallback because some browsers skip "end").
function speakText(text, onDone) {
  const synth = typeof window !== "undefined" ? window.speechSynthesis : null;
  let done = false;
  const finish = () => { if (!done) { done = true; onDone?.(); } };
  if (!synth || !text) { setTimeout(finish, 300); return; }
  synth.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-IN";
  const voices = synth.getVoices();
  u.voice = voices.find((v) => v.lang === "en-IN") || voices.find((v) => v.lang?.startsWith("en")) || null;
  u.rate = 1.02;
  u.onend = finish;
  u.onerror = finish;
  synth.speak(u);
  setTimeout(finish, 2500 + text.length * 85);
}

const BROWSER_ERRORS = {
  "not-allowed": "Microphone blocked. Allow microphone access for this page (icon in the address bar), or type your question below.",
  "service-not-allowed": "Microphone blocked. Allow microphone access for this page, or type your question below.",
  "no-speech": "I didn't hear anything. Press Ask and speak, or type your question below.",
  "audio-capture": "No microphone found. Type your question below.",
  network: "Speech recognition needs internet. Type your question below instead.",
};

export default function VoiceAssistant() {
  const [engine, setEngine] = useState(null);          // null (loading) | "browser" | "sarvam"
  const [phase, setPhase] = useState("idle");          // idle | recording | thinking | speaking | done | error
  const [seconds, setSeconds] = useState(0);
  const [interim, setInterim] = useState("");
  const [typed, setTyped] = useState("");
  const [turns, setTurns] = useState([]);              // the conversation so far
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [open, setOpen] = useState(false);
  const [keepTalking, setKeepTalking] = useState(true);
  const rec = useRef(null);
  const chunks = useRef([]);
  const timer = useRef(null);
  const player = useRef(null);
  const recog = useRef(null);
  const context = useRef(null);                        // what "yes" / "it" / "next one" refer to
  const live = useRef({ open: false, keepTalking: true, engine: null });
  live.current = { open, keepTalking, engine };
  const thread = useRef(null);

  useEffect(() => {
    api.voiceConfig().then((c) => setEngine(c.enabled ? c.engine || "sarvam" : null)).catch(() => setEngine(null));
  }, []);
  useEffect(() => () => {
    clearInterval(timer.current); player.current?.pause(); window.speechSynthesis?.cancel(); recog.current?.abort?.();
    rec.current?.stream?.getTracks().forEach((t) => t.stop());
  }, []);
  useEffect(() => { thread.current?.scrollTo?.({ top: thread.current.scrollHeight, behavior: "smooth" }); }, [turns, phase]);

  if (!engine) return null;
  const micAvailable = engine === "sarvam" || !!SR;
  const last = turns[turns.length - 1];

  const stopSound = () => { player.current?.pause(); window.speechSynthesis?.cancel(); };

  const addTurn = (r) => {
    setTurns((t) => [...t.slice(-5), r]);
    setNotice(null);
    if (r.action?.auto && r.action.path) go(r.action.path);
  };

  const playSarvam = (r) => {
    if (r?.audio) {
      player.current?.pause();
      player.current = new Audio(`data:${r.audio_type || "audio/wav"};base64,${r.audio}`);
      player.current.play().catch(() => {});
    } else speakText(r?.answer);
  };
  const spoken = (r) => [r.answer, r.follow_up].filter(Boolean).join(" ");
  const replay = (r) => (r.audio ? playSarvam(r) : speakText(spoken(r)));

  // ── typed or browser-heard text → /voice/text, then speak and (in a conversation) listen again
  const askText = async (text) => {
    const q = (text || "").trim();
    if (!q) return;
    setPhase("thinking"); setError(null); setInterim("");
    try {
      const r = await api.voiceText(q, context.current);
      context.current = r.end ? null : r.context || null;
      addTurn(r);
      setPhase("speaking");
      speakText(spoken(r), () => {
        const s = live.current;
        if (r.follow_up && !r.end && s.keepTalking && s.open && s.engine === "browser" && SR) startBrowser(true);
        else setPhase("done");
      });
    } catch (e) {
      setError(e.message); setPhase("error");
    }
  };

  // ── browser speech recognition (auto = listening for the answer to a follow-up question)
  const startBrowser = (auto = false) => {
    const r = new SR();
    r.lang = "en-IN";
    r.interimResults = true;
    r.maxAlternatives = 1;
    let finalText = "";
    r.onresult = (e) => {
      let livePart = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) finalText += e.results[i][0].transcript;
        else livePart += e.results[i][0].transcript;
      }
      setInterim(finalText + livePart);
    };
    r.onerror = (e) => {
      finalText = "";
      if (e.error === "aborted") return;
      if (auto && e.error === "no-speech") {        // no reply to the follow-up: pause quietly
        setNotice("Conversation paused. Press Ask or type to continue.");
        setPhase("done");
        return;
      }
      setError(BROWSER_ERRORS[e.error] || `Speech recognition stopped (${e.error}). Type your question below.`);
      setPhase("error");
    };
    r.onend = () => {
      recog.current = null;
      if (finalText.trim()) askText(finalText);
      else setPhase((p) => (p === "recording" ? "done" : p));
    };
    recog.current = r;
    setInterim("");
    setPhase("recording");
    try { r.start(); } catch { setError("Couldn't start the microphone. Type your question below."); setPhase("error"); }
  };

  // ── Sarvam: record audio → /voice/ask
  const sendSarvam = async (blob) => {
    setPhase("thinking");
    try {
      const r = await api.voiceAsk(blob);
      addTurn(r); setPhase("done");
      playSarvam(r);
    } catch (e) {
      if (SR) {       // Sarvam unavailable (e.g. key rejected): use the browser's voice from now on
        setEngine("browser");
        setNotice("Sarvam isn't available right now, so I switched to the browser's voice (English). Press Ask again.");
        setPhase("idle");
      } else { setError(e.message); setPhase("error"); }
    }
  };

  const startSarvam = async () => {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("Microphone blocked. Allow microphone access for this page (icon in the address bar), or type your question below.");
      setPhase("error");
      return;
    }
    const mime = pickMime();
    const r = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    chunks.current = [];
    r.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
    r.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      clearInterval(timer.current);
      const blob = new Blob(chunks.current, { type: (r.mimeType || "audio/webm").split(";")[0] });
      if (blob.size < 500) { setError("That was too short. Hold the question for a second or two."); setPhase("error"); return; }
      sendSarvam(blob);
    };
    rec.current = r;
    r.start();
    setSeconds(0); setPhase("recording");
    timer.current = setInterval(() => setSeconds((s) => {
      if (s + 1 >= MAX_SECONDS) r.state === "recording" && r.stop();
      return s + 1;
    }), 1000);
  };

  const start = () => {
    setError(null); setOpen(true); stopSound();
    if (!micAvailable) { setPhase("idle"); return; }     // no speech recognition in this browser: typing only
    engine === "browser" ? startBrowser() : startSarvam();
  };
  const stop = () => {
    if (engine === "browser") recog.current?.stop();
    else if (rec.current?.state === "recording") rec.current.stop();
  };
  const close = () => {
    setOpen(false); stopSound(); recog.current?.abort?.();
    context.current = null; setTurns([]); setPhase("idle"); setNotice(null); setError(null);
  };

  const recording = phase === "recording";
  const busy = phase === "thinking";
  const submitTyped = (e) => { e.preventDefault(); const q = typed; setTyped(""); stopSound(); recog.current?.abort?.(); askText(q); };

  return (
    <div className="relative">
      <Button kind={recording ? "danger" : "quiet"} onClick={recording ? stop : start} disabled={busy} aria-pressed={recording}
        title={engine === "sarvam" ? "Ask OrderGuard by voice (English or Hindi)" : "Ask OrderGuard by voice (English)"}>
        <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
        </svg>
        <span className="ml-1.5">
          {recording ? (engine === "sarvam" ? `Stop (${MAX_SECONDS - seconds}s)` : "Stop") : busy ? "Thinking…" : "Ask"}
        </span>
      </Button>
      {open && (
        <div role="dialog" aria-label="Voice assistant"
          className="absolute right-0 z-40 mt-2 w-[min(92vw,440px)] rounded-xl bg-deck p-4 text-[14px] shadow-[0_18px_50px_-15px_rgba(0,0,0,.8)] ring-1 ring-line-strong">
          <div className="flex items-start justify-between gap-3">
            <p className="font-cond text-[17px] font-semibold">Voice assistant</p>
            <button onClick={close} className="text-muted hover:text-ink" aria-label="Close">✕</button>
          </div>

          <div ref={thread} aria-live="polite" className="mt-2 max-h-[52vh] space-y-3 overflow-y-auto pr-1">
            {turns.map((r, i) => {
              const latest = i === turns.length - 1;
              return (
                <div key={i} className={latest ? "" : "opacity-60"}>
                  <p className="text-[12px] text-dim">You{r.language === "hi-IN" ? " (Hindi)" : ""}</p>
                  <p className="text-ink">{r.heard || "…"}</p>
                  {r.heard_english && <p className="text-[12.5px] text-dim">“{r.heard_english}”</p>}
                  <div className="mt-1.5 rounded-lg bg-night/60 p-3 ring-1 ring-line">
                    <p className="text-[12px] text-signal">OrderGuard · from live case data</p>
                    <p className="mt-0.5 leading-snug">{r.answer}</p>
                    {r.answer_english && <p className="mt-1 text-[12.5px] text-dim">{r.answer_english}</p>}
                    {r.follow_up && <p className="mt-1.5 font-medium text-signal">{r.follow_up}</p>}
                  </div>
                  {latest && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {r.action?.path && !r.action.auto && (
                        <Button size="sm" kind="primary" onClick={() => { go(r.action.path); }}>{r.action.label}</Button>
                      )}
                      <Button size="sm" onClick={() => replay(r)}>Play again</Button>
                    </div>
                  )}
                </div>
              );
            })}

            {notice && <p className="text-warn">{notice}</p>}
            {recording && (
              <p className="flex items-center gap-2 text-ink">
                <span className="pulse-dot inline-block size-2 rounded-full bg-crit" aria-hidden />
                {engine === "sarvam" ? "Listening… speak in English or हिन्दी, then press Stop."
                  : last?.follow_up ? "Listening for your answer… (say “yes”, “no thanks”, or ask anything)" : "Listening… ask in English. It stops when you pause."}
              </p>
            )}
            {recording && interim && <p className="text-ink">“{interim}”</p>}
            {busy && <p className="text-muted">Understanding…</p>}
            {phase === "speaking" && <p className="text-[12.5px] text-dim">Speaking…</p>}
            {phase === "error" && <p role="alert" className="text-crit">{error}</p>}
            {phase === "idle" && !turns.length && !notice && (
              <p className="text-muted">{micAvailable ? "Press Ask and speak, or type below." : "Voice input needs Chrome or Edge. Type your question below."}</p>
            )}
          </div>

          <form onSubmit={submitTyped} className="mt-3 flex gap-2">
            <label htmlFor="voice-typed" className="sr-only">Type a question</label>
            <input id="voice-typed" value={typed} onChange={(e) => setTyped(e.target.value)}
              placeholder={last?.follow_up ? "Type your answer…" : "Or type a question…"}
              className="min-w-0 flex-1 rounded-lg bg-night px-3 py-1.5 text-[14px] text-ink ring-1 ring-line placeholder:text-dim focus:outline-none focus:ring-signal" />
            <Button size="sm" type="submit" disabled={busy || !typed.trim()}>Send</Button>
          </form>
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[12px] text-dim">
            {engine === "browser" && SR ? (
              <label className="flex cursor-pointer items-center gap-1.5">
                <input type="checkbox" checked={keepTalking} onChange={(e) => setKeepTalking(e.target.checked)} className="accent-[#2dd4bf]" />
                Keep the conversation going
              </label>
            ) : <span />}
            <span>Voice never approves; you confirm on screen.</span>
          </div>
          {!turns.length && (
            <p className="mt-2 text-[12px] text-dim">
              Try: {EXAMPLES.map((q, i) => (
                <span key={q}>
                  <button type="button" className="underline decoration-dotted hover:text-ink" onClick={() => { stopSound(); askText(q); }}>“{q}”</button>
                  {i < EXAMPLES.length - 1 ? ", " : ""}
                </span>
              ))}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
