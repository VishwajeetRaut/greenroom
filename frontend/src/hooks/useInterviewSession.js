import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { supabase } from "../lib/supabaseClient";
import { useSpeechRecognition } from "./useSpeechRecognition";
import { useSpeechSynthesis } from "./useSpeechSynthesis";

function generateBoardDescription(elements) {
  if (!elements || elements.length === 0) return null;
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
  if (components.length === 0 && connections.length === 0) return null;
  const parts = ["[Architecture diagram]"];
  if (components.length) parts.push(`Components: ${components.join(", ")}`);
  if (connections.length) parts.push(`Connections: ${connections.join(", ")}`);
  return parts.join("\n");
}

function isDiagramMeaningful(elements) {
  const shapeTypes = new Set(["rectangle", "ellipse", "diamond", "triangle", "trapezoid", "parallelogram"]);
  const shapes = elements.filter((el) => shapeTypes.has(el.type));
  const arrows = elements.filter(
    (el) => el.type === "arrow" && el.startBinding?.elementId && el.endBinding?.elementId
  );
  return shapes.length >= 2 && arrows.length >= 1;
}

/**
 * Manages interview session lifecycle: init, send message, end.
 *
 * @param track         interview track ("behavioral" | "technical" | "system-design")
 * @param boardRef      ref to the SystemDesignBoard, used to read diagram elements
 * @param onQuestionContext  called with (QuestionContext, sessionId) when the problem is first assigned
 */
export function useInterviewSession({ track, boardRef, onQuestionContext }) {
  const navigate = useNavigate();

  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [ending, setEnding] = useState(false);
  const [answerText, setAnswerText] = useState("");
  const [diagramWarning, setDiagramWarning] = useState(null);

  const transcriptEndRef = useRef(null);
  const initDoneRef = useRef(false);

  const { isSupported, isListening, transcript, interimTranscript, start, stop, reset } =
    useSpeechRecognition();
  const { isSpeaking, speak, stop: stopSpeech } = useSpeechSynthesis();
  const [isMuted, setIsMuted] = useState(false);
  const isMutedRef = useRef(false);
  const lastInterviewerTextRef = useRef(null);

  useEffect(() => () => stopSpeech(), [stopSpeech]);

  const speakIfUnmuted = useCallback((text) => {
    lastInterviewerTextRef.current = text;
    if (!isMutedRef.current) speak(text);
  }, [speak]);

  useEffect(() => {
    if (isListening) setAnswerText(`${transcript} ${interimTranscript}`.trim());
  }, [isListening, transcript, interimTranscript]);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (initDoneRef.current) return;
    initDoneRef.current = true;
    async function init() {
      try {
        const { data: { session } } = await supabase.auth.getSession();
        if (!session) { navigate("/login", { replace: true }); return; }
        const jd = sessionStorage.getItem("interview_jd") || undefined;
        sessionStorage.removeItem("interview_jd");
        const res = await api.startSession({ track, role: "Software Engineer", job_description: jd });
        setSessionId(res.session_id);
        setMessages([{ role: "interviewer", text: res.question }]);
        speakIfUnmuted(res.question);
      } catch (err) {
        if (err.message?.includes("401") || err.message?.includes("403")) {
          navigate("/login", { replace: true }); return;
        }
        if (err.message?.includes("429")) {
          setMessages([{ role: "interviewer", text: "You have too many active sessions. Please end an existing session from your dashboard and try again." }]);
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

  const handleStartRecording = useCallback(() => { setAnswerText(""); reset(); start(); }, [reset, start]);

  // Spacebar push-to-talk: hold to record, release to stop
  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.code !== "Space" || e.repeat) return;
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      e.preventDefault();
      if (!isListening && isSupported) handleStartRecording();
    };
    const onKeyUp = (e) => {
      if (e.code !== "Space") return;
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (isListening) stop();
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, [isListening, isSupported, handleStartRecording, stop]);

  /**
   * Send a candidate message.
   * @param {{ code?: string, language?: string }} extras  pass code/language for technical track
   */
  const handleSend = async (extras = {}) => {
    const answer = answerText.trim();
    if (!answer || !sessionId) return;
    if (isListening) stop();

    let messageToSend = answer;
    if (track === "system-design" && boardRef?.current) {
      const elements = boardRef.current.getElements();
      if (elements.length > 0 && !isDiagramMeaningful(elements)) {
        setDiagramWarning(
          "Your diagram has fewer than 2 connected components. Add more structure, or dismiss this warning to send anyway."
        );
        setSending(false);
        return;
      }
      setDiagramWarning(null);
      const desc = generateBoardDescription(elements);
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
        code: extras.code,
        language: extras.language,
      });
      setMessages((prev) => [...prev, { role: "interviewer", text: res.question }]);
      speakIfUnmuted(res.question);
      if (res.question_context && onQuestionContext) onQuestionContext(res.question_context, sessionId);
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "interviewer", text: "Hmm, I lost connection there. Could you say that again?" },
      ]);
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
      setMessages((prev) => [
        ...prev,
        { role: "interviewer", text: "I had trouble generating your report. Please try ending the session again." },
      ]);
    }
  };

  const toggleMute = () => {
    const nowMuted = !isMuted;
    isMutedRef.current = nowMuted;
    setIsMuted(nowMuted);
    if (nowMuted) stopSpeech();
    else if (lastInterviewerTextRef.current) speak(lastInterviewerTextRef.current);
  };

  return {
    sessionId,
    messages,
    loading,
    sending,
    ending,
    answerText,
    setAnswerText,
    transcriptEndRef,
    isSupported,
    isListening,
    isSpeaking,
    isMuted,
    diagramWarning,
    setDiagramWarning,
    handleStartRecording,
    handleSend,
    handleEnd,
    toggleMute,
    stop,
  };
}
