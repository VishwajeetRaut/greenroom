import { useEffect, useRef } from "react";
import Waveform from "./Waveform";

const TRACK_LABELS = {
  behavioral: "Behavioral",
  technical: "Technical",
  "system-design": "System design",
};

export default function ChatPanel({
  track,
  messages,
  loading,
  sending,
  ending,
  sessionId,
  answerText,
  onAnswerChange,
  onSend,
  onEnd,
  isMuted,
  onMuteToggle,
  isSpeaking,
  isListening,
  isSupported,
  onStartRecording,
  onStopRecording,
  sessionFull,
}) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <section className="flex flex-col rounded-2xl border border-white/10 bg-panel">
      <PanelHeader
        track={track}
        isMuted={isMuted}
        onMuteToggle={onMuteToggle}
        ending={ending}
        sessionId={sessionId}
        onEnd={onEnd}
      />

      <MessageList
        messages={messages}
        loading={loading}
        isSpeaking={isSpeaking}
        isMuted={isMuted}
        bottomRef={bottomRef}
      />

      {sessionFull && (
        <div className="mx-5 mb-2 rounded-lg border border-amber/30 bg-amber/5 px-4 py-2 text-xs text-amber-300">
          You've reached the session limit. Click <strong>End session</strong> to get your scored evaluation.
        </div>
      )}

      <AnswerInput
        answerText={answerText}
        onAnswerChange={onAnswerChange}
        onSend={onSend}
        sending={sending}
        isListening={isListening}
        isSupported={isSupported}
        onStartRecording={onStartRecording}
        onStopRecording={onStopRecording}
        disabled={sessionFull}
      />
    </section>
  );
}

function PanelHeader({ track, isMuted, onMuteToggle, ending, sessionId, onEnd }) {
  return (
    <div className="flex items-center justify-between border-b border-white/5 px-5 py-4">
      <div className="flex items-center gap-2 text-sm text-mute">
        <span className="h-2 w-2 rounded-full bg-sage" />
        {TRACK_LABELS[track] ?? "Interview"} session
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={onMuteToggle}
          title={isMuted ? "Unmute interviewer" : "Mute interviewer"}
          className="rounded-full border border-white/10 px-3 py-1.5 text-xs text-mute transition hover:border-white/30 hover:text-cream"
        >
          {isMuted ? "🔇 Muted" : "🔊 Mute"}
        </button>
        <button
          onClick={onEnd}
          disabled={ending || !sessionId}
          className="rounded-full border border-white/10 px-4 py-1.5 text-xs text-mute transition hover:border-coral/40 hover:text-coral disabled:opacity-50"
        >
          {ending ? "Wrapping up..." : "End session"}
        </button>
      </div>
    </div>
  );
}

function MessageList({ messages, loading, isSpeaking, isMuted, bottomRef }) {
  return (
    <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5" style={{ maxHeight: "55vh" }}>
      {loading && <p className="text-sm text-mute">Setting up your interviewer...</p>}
      {messages.map((m, i) => (
        <div
          key={i}
          className={
            m.role === "interviewer"
              ? "rounded-xl bg-panelLight/60 p-4 text-sm"
              : "rounded-xl border border-amber/20 bg-amber/5 p-4 text-sm"
          }
        >
          <p className={`font-display ${m.role === "interviewer" ? "text-cream" : "text-amber"}`}>
            {m.role === "interviewer" ? "Interviewer" : "You"}
          </p>
          <p className="mt-1 text-cream/90">{m.text}</p>
        </div>
      ))}
      {isSpeaking && !isMuted && (
        <p className="text-xs text-mute">Interviewer is speaking...</p>
      )}
      <div ref={bottomRef} />
    </div>
  );
}

function AnswerInput({
  answerText, onAnswerChange, onSend, sending,
  isListening, isSupported, onStartRecording, onStopRecording,
  disabled,
}) {
  return (
    <div className="border-t border-white/5 p-5">
      {!isSupported && (
        <p className="mb-3 text-xs text-coral">
          Your browser doesn't support live speech recognition. Try Chrome or Edge, or type below.
        </p>
      )}

      <div className="rounded-xl border border-white/10 bg-panelLight/40 p-4">
        <div className="flex items-center justify-between">
          <span className="text-xs uppercase tracking-wide text-mute">Your answer</span>
          {isListening && <Waveform active size="sm" />}
        </div>
        <textarea
          value={answerText}
          onChange={(e) => onAnswerChange(e.target.value)}
          readOnly={isListening}
          disabled={disabled}
          placeholder={disabled ? "Session complete — click End session above" : "Press the mic and speak, or type here"}
          className="mt-2 w-full resize-none rounded-lg bg-transparent text-sm text-cream outline-none disabled:opacity-50"
          rows={3}
        />
      </div>

      <div className="mt-4 flex items-center gap-3">
        <button
          onClick={isListening ? onStopRecording : onStartRecording}
          disabled={!isSupported || disabled}
          className={`rounded-full px-5 py-2.5 text-sm font-medium transition ${
            isListening ? "bg-coral text-ink" : "bg-amber text-ink hover:bg-amberDark"
          } disabled:opacity-50`}
        >
          {isListening ? "Stop recording" : "Record answer"}
        </button>
        <button
          onClick={onSend}
          disabled={sending || !answerText.trim() || disabled}
          className="rounded-full border border-white/10 px-5 py-2.5 text-sm text-cream transition hover:border-amber/40 disabled:opacity-50"
        >
          {sending ? "Sending..." : "Send answer"}
        </button>
      </div>
    </div>
  );
}
