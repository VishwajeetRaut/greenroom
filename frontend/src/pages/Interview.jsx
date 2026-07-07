import { lazy, Suspense, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import Navbar from "../components/Navbar";
import Waveform from "../components/Waveform";
import CodeEditor from "../components/CodeEditor";
import { useInterviewSession } from "../hooks/useInterviewSession";
import { useCodeRunner } from "../hooks/useCodeRunner";

const SystemDesignBoard = lazy(() => import("../components/SystemDesignBoard"));

const TRACK_LABELS = {
  behavioral: "Behavioral",
  technical: "Technical",
  "system-design": "System design",
};

export default function Interview() {
  const [params] = useSearchParams();
  const track = params.get("track") || "behavioral";
  const boardRef = useRef(null);

  const codeRunner = useCodeRunner();
  const session = useInterviewSession({
    track,
    boardRef,
    onQuestionContext: codeRunner.setQuestionContext,
  });

  return (
    <div className="flex min-h-screen flex-col bg-stage">
      <Navbar />
      <main className="flex-1">
        <div className="mx-auto grid max-w-6xl grid-cols-1 gap-6 px-6 py-8 lg:grid-cols-[1.1fr_1fr]">

          {/* ── Conversation column ── */}
          <section className="flex flex-col rounded-2xl border border-white/10 bg-panel">
            <div className="flex items-center justify-between border-b border-white/5 px-5 py-4">
              <div className="flex items-center gap-2 text-sm text-mute">
                <span className="h-2 w-2 rounded-full bg-sage" />
                {TRACK_LABELS[track] || "Interview"} session
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={session.toggleMute}
                  title={session.isMuted ? "Unmute interviewer" : "Mute interviewer"}
                  className="rounded-full border border-white/10 px-3 py-1.5 text-xs text-mute transition hover:border-white/30 hover:text-cream"
                >
                  {session.isMuted ? "🔇 Muted" : "🔊 Mute"}
                </button>
                <button
                  onClick={session.handleEnd}
                  disabled={session.ending || !session.sessionId}
                  className="rounded-full border border-white/10 px-4 py-1.5 text-xs text-mute transition hover:border-coral/40 hover:text-coral disabled:opacity-50"
                >
                  {session.ending ? "Wrapping up..." : "End session"}
                </button>
              </div>
            </div>

            <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5" style={{ maxHeight: "55vh" }}>
              {session.loading && <p className="text-sm text-mute">Setting up your interviewer...</p>}
              {session.messages.map((m, i) => (
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
              {session.isSpeaking && !session.isMuted && (
                <p className="text-xs text-mute">Interviewer is speaking...</p>
              )}
              <div ref={session.transcriptEndRef} />
            </div>

            <div className="border-t border-white/5 p-5">
              {!session.isSupported && (
                <p className="mb-3 text-xs text-coral">
                  Your browser doesn't support live speech recognition. Try Chrome or Edge, or
                  type your answer below.
                </p>
              )}

              {session.diagramWarning && (
                <div className="mb-3 flex items-start justify-between gap-2 rounded-lg border border-amber/30 bg-amber/5 px-3 py-2 text-xs text-amber-300/80">
                  <span>{session.diagramWarning}</span>
                  <button
                    onClick={() => session.setDiagramWarning(null)}
                    className="shrink-0 text-white/40 hover:text-white/70"
                  >
                    ✕
                  </button>
                </div>
              )}

              <div className="rounded-xl border border-white/10 bg-panelLight/40 p-4">
                <div className="flex items-center justify-between">
                  <span className="text-xs uppercase tracking-wide text-mute">Your answer</span>
                  {session.isListening && <Waveform active size="sm" />}
                </div>
                <textarea
                  value={session.answerText}
                  onChange={(e) => session.setAnswerText(e.target.value)}
                  readOnly={session.isListening}
                  placeholder="Press the mic and speak, or type here"
                  className="mt-2 w-full resize-none rounded-lg bg-transparent text-sm text-cream outline-none"
                  rows={3}
                />
              </div>

              <div className="mt-4 flex items-center gap-3">
                <button
                  onClick={session.isListening ? session.stop : session.handleStartRecording}
                  disabled={!session.isSupported}
                  className={`rounded-full px-5 py-2.5 text-sm font-medium transition ${
                    session.isListening ? "bg-coral text-ink" : "bg-amber text-ink hover:bg-amberDark"
                  } disabled:opacity-50`}
                >
                  {session.isListening ? "Stop recording" : "Record answer"}
                </button>
                <button
                  onClick={() =>
                    session.handleSend(
                      track === "technical"
                        ? { code: codeRunner.code, language: codeRunner.language }
                        : {}
                    )
                  }
                  disabled={session.sending || !session.answerText.trim()}
                  className="rounded-full border border-white/10 px-5 py-2.5 text-sm text-cream transition hover:border-amber/40 disabled:opacity-50"
                >
                  {session.sending ? "Sending..." : "Send answer"}
                </button>
              </div>
            </div>
          </section>

          {/* ── Side column ── */}
          <section className="rounded-2xl border border-white/10 bg-panel">
            {track === "technical" ? (
              <CodeEditor
                language={codeRunner.language}
                code={codeRunner.code}
                setCode={codeRunner.setCode}
                running={codeRunner.running}
                slowHint={codeRunner.slowHint}
                testResults={codeRunner.testResults}
                revealedCount={codeRunner.revealedCount}
                boilerplateNote={codeRunner.boilerplateNote}
                questionContext={codeRunner.questionContext}
                onLanguageChange={(lang) => codeRunner.handleLanguageChange(lang, session.sessionId)}
                onRun={() => codeRunner.handleRunCode(session.sessionId)}
              />
            ) : track === "system-design" ? (
              <Suspense fallback={<div className="p-6 text-sm text-mute">Loading board…</div>}>
                <SystemDesignBoard ref={boardRef} />
              </Suspense>
            ) : (
  <div className="flex h-full flex-col p-6">
    <h2 className="font-display text-xl">During this session</h2>
    <p className="mt-1 text-xs text-mute">Tips to get the most out of your practice</p>

    <div className="mt-6 space-y-3">
      <div className="rounded-xl border border-white/5 bg-panelLight/40 p-4">
        <p className="text-sm font-medium text-cream">🎙 Speak naturally</p>
        <p className="mt-1 text-xs text-mute">The interviewer responds to what you actually say — no scripted replies.</p>
      </div>
      <div className="rounded-xl border border-white/5 bg-panelLight/40 p-4">
        <p className="text-sm font-medium text-cream">⏸ Pause when you need to</p>
        <p className="mt-1 text-xs text-mute">There's no penalty for taking a breath before answering.</p>
      </div>
      <div className="rounded-xl border border-white/5 bg-panelLight/40 p-4">
        <p className="text-sm font-medium text-cream">⭐ Use the STAR method</p>
        <p className="mt-1 text-xs text-mute">Structure your answers — Situation, Task, Action, Result — for clearer storytelling.</p>
      </div>
      <div className="rounded-xl border border-white/5 bg-panelLight/40 p-4">
        <p className="text-sm font-medium text-cream">🏁 End when you're ready</p>
        <p className="mt-1 text-xs text-mute">Hit "End session" whenever you want your full feedback report.</p>
      </div>
    </div>

    <div className="mt-auto pt-6">
      <div className="rounded-xl border border-amber/20 bg-amber/5 p-4">
        <p className="text-xs font-medium text-amber">Reminder</p>
        <p className="mt-1 text-xs text-mute">Your session is being recorded for feedback. Be as detailed as you would in a real interview.</p>
      </div>
    </div>
  </div>
)}
          </section>

        </div>
      </main>
    </div>
  );
}
