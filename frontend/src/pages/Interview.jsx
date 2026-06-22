import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import Editor from "@monaco-editor/react";
import Navbar from "../components/Navbar";
import Waveform from "../components/Waveform";
import { api } from "../lib/api";
import { supabase } from "../lib/supabaseClient";
import { useSpeechRecognition } from "../hooks/useSpeechRecognition";
import { useSpeechSynthesis } from "../hooks/useSpeechSynthesis";

const TRACK_LABELS = {
  behavioral: "Behavioral",
  technical: "Technical",
  "system-design": "System design"
};

const LANGUAGES = [
  { id: "python",     label: "Python",     monaco: "python",     piston: "python", version: "3.10.0"  },
  { id: "javascript", label: "JavaScript", monaco: "javascript", piston: "node",   version: "18.15.0" },
  { id: "java",       label: "Java",       monaco: "java",       piston: "java",   version: "15.0.2"  },
  { id: "cpp",        label: "C++",        monaco: "cpp",        piston: "gcc",    version: "10.2.0"  },
];

const STARTER_CODE = {
  python: `def two_sum(nums: list[int], target: int) -> list[int]:
    # Write your solution here
    pass
`,
  javascript: `/**
 * @param {number[]} nums
 * @param {number} target
 * @return {number[]}
 */
function twoSum(nums, target) {

}
`,
  java: `class Solution {
    public int[] twoSum(int[] nums, int target) {
        // Write your solution here
        return new int[]{};
    }
}
`,
  cpp: `#include <vector>
#include <unordered_map>
using namespace std;

class Solution {
public:
    vector<int> twoSum(vector<int>& nums, int target) {
        // Write your solution here
        return {};
    }
};
`,
};

export default function Interview() {
  const [params] = useSearchParams();
  const track = params.get("track") || "behavioral";
  const navigate = useNavigate();

  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [ending, setEnding] = useState(false);

  const [language, setLanguage] = useState("python");
  const [code, setCode] = useState(STARTER_CODE.python);
  const [testResults, setTestResults] = useState(null);
  const [revealedCount, setRevealedCount] = useState(0);
  const [running, setRunning] = useState(false);

  const { isSupported, isListening, transcript, interimTranscript, start, stop, reset } =
    useSpeechRecognition();
  const { isSpeaking, speak, stop: stopSpeech } = useSpeechSynthesis();

  const [answerText, setAnswerText] = useState("");
  const transcriptEndRef = useRef(null);
  const initDoneRef = useRef(false);

  useEffect(() => {
    if (isListening) {
      setAnswerText(`${transcript} ${interimTranscript}`.trim());
    }
  }, [isListening, transcript, interimTranscript]);

  const handleStartRecording = () => {
    setAnswerText("");
    reset();
    start();
  };

  useEffect(() => {
    if (initDoneRef.current) return;
    initDoneRef.current = true;

    async function init() {
      try {
        const { data: userData } = await supabase.auth.getUser();
        const res = await api.startSession({
          track,
          role: "Software Engineer",
          user_id: userData?.user?.id
        });
        setSessionId(res.session_id);
        setMessages([{ role: "interviewer", text: res.question }]);
        speak(res.question);
      } catch (err) {
        setMessages([
          {
            role: "interviewer",
            text:
              "I couldn't reach the interview backend. Make sure the API server is running and try again."
          }
        ]);
      } finally {
        setLoading(false);
      }
    }
    init();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = async () => {
    const answer = answerText.trim();
    if (!answer || !sessionId) return;

    if (isListening) stop();
    setMessages((prev) => [...prev, { role: "candidate", text: answer }]);
    setAnswerText("");
    reset();
    setSending(true);

    try {
      const res = await api.sendMessage({
        session_id: sessionId,
        message: answer,
        code: track === "technical" ? code : undefined,
        language: track === "technical" ? language : undefined
      });

      setMessages((prev) => [...prev, { role: "interviewer", text: res.question }]);
      speak(res.question);
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "interviewer", text: "Hmm, I lost connection there. Could you say that again?" }
      ]);
    } finally {
      setSending(false);
    }
  };

  const handleRunCode = async () => {
    const lang = LANGUAGES.find((l) => l.id === language);
    setRunning(true);
    setTestResults(null);
    setRevealedCount(0);

    try {
      const res = await api.runTests({ language: lang.piston, version: lang.version, source: code });
      setTestResults(res);
      // Reveal visible test cases one by one, then all hidden at once
      const visibleLen = res.visible_tests?.length ?? 0;
      const hiddenLen  = res.hidden_tests?.length ?? 0;
      for (let i = 1; i <= visibleLen + (hiddenLen > 0 ? 1 : 0); i++) {
        setTimeout(() => setRevealedCount(i), i * 300);
      }
    } catch {
      setTestResults({
        status: "compile_error",
        compile_error: "Could not reach the code execution service.",
        visible_tests: [],
        hidden_tests: [],
        passed: 0,
        total: 7,
      });
    } finally {
      setRunning(false);
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
        {
          role: "interviewer",
          text: "I had trouble generating your report. Please try ending the session again."
        }
      ]);
    }
  };

  return (
    <div className="flex min-h-screen flex-col bg-stage">
      <Navbar />
      <main className="flex-1">
        <div className="mx-auto grid max-w-6xl grid-cols-1 gap-6 px-6 py-8 lg:grid-cols-[1.1fr_1fr]">
          {/* Conversation column */}
          <section className="flex flex-col rounded-2xl border border-white/10 bg-panel">
            <div className="flex items-center justify-between border-b border-white/5 px-5 py-4">
              <div className="flex items-center gap-2 text-sm text-mute">
                <span className="h-2 w-2 rounded-full bg-sage" />
                {TRACK_LABELS[track] || "Interview"} session
              </div>
              <button
                onClick={handleEnd}
                disabled={ending || !sessionId}
                className="rounded-full border border-white/10 px-4 py-1.5 text-xs text-mute transition hover:border-coral/40 hover:text-coral disabled:opacity-50"
              >
                {ending ? "Wrapping up..." : "End session"}
              </button>
            </div>

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
              {isSpeaking && (
                <p className="text-xs text-mute">Interviewer is speaking...</p>
              )}
              <div ref={transcriptEndRef} />
            </div>

            <div className="border-t border-white/5 p-5">
              {!isSupported && (
                <p className="mb-3 text-xs text-coral">
                  Your browser doesn't support live speech recognition. Try Chrome or Edge, or
                  type your answer below.
                </p>
              )}

              <div className="rounded-xl border border-white/10 bg-panelLight/40 p-4">
                <div className="flex items-center justify-between">
                  <span className="text-xs uppercase tracking-wide text-mute">Your answer</span>
                  {isListening && <Waveform active size="sm" />}
                </div>
                <textarea
                  value={answerText}
                  onChange={(e) => setAnswerText(e.target.value)}
                  readOnly={isListening}
                  placeholder="Press the mic and speak, or type here"
                  className="mt-2 w-full resize-none rounded-lg bg-transparent text-sm text-cream outline-none"
                  rows={3}
                />
              </div>

              <div className="mt-4 flex items-center gap-3">
                <button
                  onClick={isListening ? stop : handleStartRecording}
                  disabled={!isSupported}
                  className={`rounded-full px-5 py-2.5 text-sm font-medium transition ${
                    isListening
                      ? "bg-coral text-ink"
                      : "bg-amber text-ink hover:bg-amberDark"
                  } disabled:opacity-50`}
                >
                  {isListening ? "Stop recording" : "Record answer"}
                </button>
                <button
                  onClick={handleSend}
                  disabled={sending || !answerText.trim()}
                  className="rounded-full border border-white/10 px-5 py-2.5 text-sm text-cream transition hover:border-amber/40 disabled:opacity-50"
                >
                  {sending ? "Sending..." : "Send answer"}
                </button>
              </div>
            </div>
          </section>

          {/* Side column: code editor for technical, or prep notes */}
          <section className="rounded-2xl border border-white/10 bg-panel">
            {track === "technical" ? (
              <div className="flex h-full flex-col">
                <div className="flex items-center justify-between border-b border-white/5 px-5 py-4">
                  <span className="text-sm text-mute">Code editor</span>
                  <select
                    value={language}
                    onChange={(e) => {
                      setLanguage(e.target.value);
                      setCode(STARTER_CODE[e.target.value]);
                      setTestResults(null);
                      setRevealedCount(0);
                    }}
                    className="rounded-lg border border-white/10 bg-panelLight px-3 py-1.5 text-xs text-cream"
                  >
                    {LANGUAGES.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="flex-1">
                  <Editor
                    height="320px"
                    theme="vs-dark"
                    language={LANGUAGES.find((l) => l.id === language).monaco}
                    value={code}
                    onChange={(value) => setCode(value ?? "")}
                    options={{ fontSize: 13, minimap: { enabled: false } }}
                  />
                </div>
                <div className="border-t border-white/5 p-4">
                  <button
                    onClick={handleRunCode}
                    disabled={running}
                    className="rounded-full bg-sage px-5 py-2 text-sm font-medium text-ink transition hover:opacity-90 disabled:opacity-50"
                  >
                    {running ? (
                      <span className="flex items-center gap-2">
                        <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-ink border-t-transparent" />
                        Running…
                      </span>
                    ) : "Run code"}
                  </button>

                  {running && (
                    <div className="mt-3 space-y-2">
                      {[0, 1, 2].map((i) => (
                        <div key={i} className="h-8 animate-pulse rounded-lg bg-white/5" />
                      ))}
                    </div>
                  )}

                  {testResults && !running && (
                    <div className="mt-3 overflow-hidden rounded-lg border border-white/10 bg-ink">
                      {/* Summary bar */}
                      <div className={`flex items-center justify-between px-4 py-2 text-xs font-medium ${
                        testResults.status === "accepted"
                          ? "bg-sage/15 text-sage"
                          : "bg-coral/15 text-coral"
                      }`}>
                        <span>
                          {testResults.status === "accepted" && "Accepted"}
                          {testResults.status === "wrong_answer" && "Wrong Answer"}
                          {testResults.status === "runtime_error" && "Runtime Error"}
                          {testResults.status === "compile_error" && "Compilation Error"}
                        </span>
                        <span className="text-mute">
                          {testResults.passed} / {testResults.total} passed
                        </span>
                      </div>

                      {/* Compile error body */}
                      {testResults.compile_error && (
                        <pre className="max-h-32 overflow-auto p-3 text-xs text-coral">
                          {testResults.compile_error}
                        </pre>
                      )}

                      {/* Visible test cases */}
                      {testResults.visible_tests?.slice(0, revealedCount).map((tc) => (
                        <div
                          key={tc.id}
                          className={`border-t border-white/5 p-3 transition-all duration-300 ${
                            tc.passed ? "" : "bg-coral/5"
                          }`}
                        >
                          <div className={`flex items-center gap-2 text-xs font-medium ${
                            tc.passed ? "text-sage" : "text-coral"
                          }`}>
                            <span>{tc.passed ? "✓" : "✗"}</span>
                            <span>{tc.label}</span>
                          </div>
                          {!tc.passed && (
                            <div className="mt-2 space-y-1 text-xs">
                              <p className="text-mute">
                                Input: <span className="font-mono text-cream">{tc.input}</span>
                              </p>
                              <p className="text-mute">
                                Expected: <span className="font-mono text-cream">{tc.expected}</span>
                              </p>
                              <p className="text-mute">
                                Got:{" "}
                                <span className="font-mono text-coral">
                                  {tc.error ?? tc.output ?? "no output"}
                                </span>
                              </p>
                            </div>
                          )}
                        </div>
                      ))}

                      {/* Hidden test cases revealed after visible */}
                      {revealedCount > (testResults.visible_tests?.length ?? 0) &&
                        testResults.hidden_tests?.length > 0 && (
                          <div className="border-t border-white/5 p-3">
                            <p className="mb-2 text-xs text-mute">Hidden test cases</p>
                            <div className="flex flex-wrap gap-2">
                              {testResults.hidden_tests.map((tc) => (
                                <span
                                  key={tc.id}
                                  className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs ${
                                    tc.passed
                                      ? "bg-sage/15 text-sage"
                                      : "bg-coral/15 text-coral"
                                  }`}
                                >
                                  🔒 {tc.passed ? "✓" : "✗"}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="p-6">
                <h2 className="font-display text-xl">During this session</h2>
                <ul className="mt-4 space-y-3 text-sm text-mute">
                  <li>Speak naturally. The interviewer responds to what you actually say.</li>
                  <li>Take a breath before answering. There's no penalty for a pause.</li>
                  <li>End the session whenever you're ready for your feedback report.</li>
                </ul>
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
