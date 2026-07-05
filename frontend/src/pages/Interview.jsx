import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import Navbar from "../components/Navbar";
import ChatPanel from "../components/ChatPanel";
import CodeEditor, { STARTER_CODE } from "../components/CodeEditor";
import { api } from "../lib/api";
import { supabase } from "../lib/supabaseClient";
import { useSpeechRecognition } from "../hooks/useSpeechRecognition";
import { useSpeechSynthesis } from "../hooks/useSpeechSynthesis";

const SystemDesignBoard = lazy(() => import("../components/SystemDesignBoard"));

function generateBoardDescription(elements) {
  if (!elements?.length) return null;

  const labelOf = {};
  elements.forEach((el) => {
    if (el.type === "text" && el.containerId)
      labelOf[el.containerId] = (labelOf[el.containerId] || "") + el.text?.trim();
  });
  elements.forEach((el) => {
    if (el.type === "text" && !el.containerId && el.text?.trim())
      labelOf[el.id] = el.text.trim();
  });

  const shapeTypes = new Set(["rectangle", "ellipse", "diamond", "triangle", "trapezoid", "parallelogram"]);
  const components = elements
    .filter((el) => shapeTypes.has(el.type))
    .map((el) => labelOf[el.id] || el.type)
    .filter(Boolean);
  const connections = elements
    .filter((el) => el.type === "arrow" && el.startBinding?.elementId && el.endBinding?.elementId)
    .map((el) => {
      const from = labelOf[el.startBinding.elementId] || "node";
      const to   = labelOf[el.endBinding.elementId]   || "node";
      const via  = labelOf[el.id];
      return via ? `${from} --[${via}]--> ${to}` : `${from} → ${to}`;
    });

  if (!components.length && !connections.length) return null;
  const parts = ["[Architecture diagram]"];
  if (components.length) parts.push(`Components: ${components.join(", ")}`);
  if (connections.length) parts.push(`Connections: ${connections.join(", ")}`);
  return parts.join("\n");
}

export default function Interview() {
  const [params] = useSearchParams();
  const track = params.get("track") || "behavioral";
  const navigate = useNavigate();

  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [ending, setEnding] = useState(false);
  const [sessionFull, setSessionFull] = useState(false);

  const [language, setLanguage] = useState("python");
  const [code, setCode] = useState(STARTER_CODE.python);

  const { isSupported, isListening, transcript, interimTranscript, start, stop, reset } =
    useSpeechRecognition();
  const { isSpeaking, speak, stop: stopSpeech } = useSpeechSynthesis();
  const [isMuted, setIsMuted] = useState(false);
  const isMutedRef = useRef(false);
  const lastInterviewerTextRef = useRef(null);

  const [answerText, setAnswerText] = useState("");
  const initDoneRef = useRef(false);
  const boardRef = useRef(null);

  useEffect(() => () => stopSpeech(), [stopSpeech]);

  useEffect(() => {
    if (isListening) setAnswerText(`${transcript} ${interimTranscript}`.trim());
  }, [isListening, transcript, interimTranscript]);

  const speakIfUnmuted = useCallback((text) => {
    lastInterviewerTextRef.current = text;
    if (!isMutedRef.current) speak(text);
  }, [speak]);

  useEffect(() => {
    if (initDoneRef.current) return;
    initDoneRef.current = true;

    async function init() {
      try {
        const { data: { session } } = await supabase.auth.getSession();
        if (!session) { navigate("/login", { replace: true }); return; }

        const res = await api.startSession({ track, role: "Software Engineer" });
        setSessionId(res.session_id);
        setMessages([{ role: "interviewer", text: res.question }]);
        speakIfUnmuted(res.question);
      } catch (err) {
        if (err.message?.includes("401") || err.message?.includes("403")) {
          navigate("/login", { replace: true });
          return;
        }
        setMessages([{ role: "interviewer", text: "I couldn't reach the interview backend. Make sure the API server is running and try again." }]);
      } finally {
        setLoading(false);
      }
    }
    init();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSend = async () => {
    const answer = answerText.trim();
    if (!answer || !sessionId) return;
    if (isListening) stop();

    let messageToSend = answer;
    if (track === "system-design" && boardRef.current) {
      const desc = generateBoardDescription(boardRef.current.getElements());
      if (desc) messageToSend = `${answer}\n\n${desc}`;
    }

    setMessages((prev) => [...prev, { role: "candidate", text: answer }]);
    setAnswerText("");
    reset();
    setSending(true);

    try {
      const res = await api.sendMessage({
        session_id: sessionId,
        message: messageToSend,
        code: track === "technical" ? code : undefined,
        language: track === "technical" ? language : undefined,
      });
      setMessages((prev) => [...prev, { role: "interviewer", text: res.question }]);
      speakIfUnmuted(res.question);
      if (res.done) setSessionFull(true);
    } catch {
      setMessages((prev) => [...prev, { role: "interviewer", text: "Hmm, I lost connection there. Could you say that again?" }]);
    } finally {
      setSending(false);
    }
  };

  const handleEnd = async () => {
    if (!sessionId || ending) return;
    setEnding(true);
    stopSpeech();
    stop();
    try {
      await api.endSession({ session_id: sessionId });
      navigate(`/results/${sessionId}`);
    } catch (err) {
      console.error("End session failed:", err);
      setEnding(false);
      setMessages((prev) => [...prev, { role: "interviewer", text: "I had trouble generating your report. Please try ending the session again." }]);
    }
  };

  const handleMuteToggle = () => {
    const nowMuted = !isMuted;
    isMutedRef.current = nowMuted;
    setIsMuted(nowMuted);
    if (nowMuted) {
      stopSpeech();
    } else if (lastInterviewerTextRef.current) {
      speak(lastInterviewerTextRef.current);
    }
  };

  const handleStartRecording = () => { setAnswerText(""); reset(); start(); };

  return (
    <div className="flex min-h-screen flex-col bg-stage">
      <Navbar />
      <main className="flex-1">
        <div className="mx-auto grid max-w-6xl grid-cols-1 gap-6 px-6 py-8 lg:grid-cols-[1.1fr_1fr]">
          <ChatPanel
            track={track}
            messages={messages}
            loading={loading}
            sending={sending}
            ending={ending}
            sessionId={sessionId}
            answerText={answerText}
            onAnswerChange={setAnswerText}
            onSend={handleSend}
            onEnd={handleEnd}
            isMuted={isMuted}
            onMuteToggle={handleMuteToggle}
            isSpeaking={isSpeaking}
            isListening={isListening}
            isSupported={isSupported}
            onStartRecording={handleStartRecording}
            onStopRecording={stop}
            sessionFull={sessionFull}
          />

          <section className="rounded-2xl border border-white/10 bg-panel">
            {track === "technical" ? (
              <CodeEditor
                sessionId={sessionId}
                language={language}
                code={code}
                onLanguageChange={setLanguage}
                onCodeChange={setCode}
              />
            ) : track === "system-design" ? (
              <Suspense fallback={<div className="p-6 text-sm text-mute">Loading board…</div>}>
                <SystemDesignBoard ref={boardRef} />
              </Suspense>
            ) : (
              <BehavioralTips />
            )}
          </section>
        </div>
      </main>
    </div>
  );
}

function BehavioralTips() {
  return (
    <div className="p-6">
      <h2 className="font-display text-xl">During this session</h2>
      <ul className="mt-4 space-y-3 text-sm text-mute">
        <li>Speak naturally. The interviewer responds to what you actually say.</li>
        <li>Take a breath before answering. There's no penalty for a pause.</li>
        <li>End the session whenever you're ready for your feedback report.</li>
      </ul>
    </div>
  );
}
