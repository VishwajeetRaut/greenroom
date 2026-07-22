import { useState } from "react";
import Editor from "@monaco-editor/react";
import TestResultsPanel from "./TestResultsPanel";

const LANGUAGES = [
  { id: "python",     label: "Python",     monaco: "python",     piston: "python", version: "3.10.0"  },
  { id: "javascript", label: "JavaScript", monaco: "javascript", piston: "node",   version: "18.15.0" },
  { id: "java",       label: "Java",       monaco: "java",       piston: "java",   version: "15.0.2"  },
  { id: "cpp",        label: "C++",        monaco: "cpp",        piston: "gcc",    version: "10.2.0"  },
];

export { LANGUAGES };

const DIFFICULTY_STYLES = {
  easy:   { label: "Easy",   className: "text-emerald-400 bg-emerald-400/10" },
  medium: { label: "Medium", className: "text-amber-400 bg-amber-400/10" },
  hard:   { label: "Hard",   className: "text-red-400 bg-red-400/10" },
};

function ProblemPanel({ questionContext }) {
  const [tab, setTab] = useState("description");

  if (!questionContext) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-6 py-10">
        <div className="text-3xl mb-3">⌛</div>
        <p className="text-sm font-medium text-cream/70">Waiting for problem</p>
        <p className="text-xs text-mute mt-1">The interviewer will assign a coding question shortly</p>
      </div>
    );
  }

  const diff = DIFFICULTY_STYLES[questionContext.difficulty?.toLowerCase()] || DIFFICULTY_STYLES.medium;
  const tabs = ["description", "examples", "constraints"];

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Problem header */}
      <div className="px-5 pt-4 pb-3 border-b border-white/5 shrink-0">
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="font-display text-base font-semibold text-cream">{questionContext.title}</h2>
          <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${diff.className}`}>
            {diff.label}
          </span>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mt-3">
          {tabs.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-3 py-1 text-xs rounded-md capitalize transition ${
                tab === t
                  ? "bg-white/10 text-cream font-medium"
                  : "text-mute hover:text-cream"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto px-5 py-4 text-sm text-cream/80 space-y-3">
        {tab === "description" && (
          <p className="leading-relaxed whitespace-pre-wrap">{questionContext.prompt}</p>
        )}

        {tab === "examples" && (
          <div className="space-y-4">
            {(questionContext.examples || []).length === 0 ? (
              <p className="text-mute text-xs">No examples available.</p>
            ) : (
              questionContext.examples.map((ex, i) => (
                <div key={i} className="rounded-lg bg-white/5 p-3 space-y-1.5">
                  <p className="text-xs font-semibold text-cream/60 uppercase tracking-wide">Example {i + 1}</p>
                  <div className="font-mono text-xs space-y-1">
                    <div>
                      <span className="text-mute">Input: </span>
                      <span className="text-cream">{String(ex.input ?? "")}</span>
                    </div>
                    <div>
                      <span className="text-mute">Output: </span>
                      <span className="text-cream">{String(ex.output ?? "")}</span>
                    </div>
                    {ex.explanation && (
                      <div className="pt-1 text-cream/60">
                        <span className="text-mute">Explanation: </span>
                        {ex.explanation}
                      </div>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {tab === "constraints" && (
          <ul className="space-y-1.5">
            {(questionContext.constraints || []).length === 0 ? (
              <li className="text-mute text-xs">No constraints listed.</li>
            ) : (
              questionContext.constraints.map((c, i) => (
                <li key={i} className="flex items-start gap-2">
                  <span className="text-sage mt-0.5 shrink-0">•</span>
                  <span className="font-mono text-xs text-cream/80">{c}</span>
                </li>
              ))
            )}
          </ul>
        )}
      </div>
    </div>
  );
}

export default function CodeEditor({
  language,
  code,
  setCode,
  running,
  slowHint,
  testResults,
  revealedCount,
  boilerplateNote,
  questionContext,
  onLanguageChange,
  onRun,
  onReset,
}) {
  const lang = LANGUAGES.find((l) => l.id === language);

  return (
    <div className="flex flex-col h-full">

      {/* ── Problem panel ── */}
      <div className="border-b border-white/5" style={{ height: "42%" }}>
        <ProblemPanel questionContext={questionContext} />
      </div>

      {/* ── Editor panel ── */}
      <div className="flex flex-col flex-1 min-h-0">

        {/* Editor toolbar */}
        <div className="flex items-center justify-between border-b border-white/5 px-4 py-2 shrink-0">
          <span className="text-xs text-mute">Code editor</span>
          <div className="flex items-center gap-2">
            {boilerplateNote && (
              <span className="text-xs text-amber-300/80">{boilerplateNote}</span>
            )}
            <button
              onClick={onReset}
              disabled={running}
              title="Reset to original boilerplate"
              className="rounded-lg border border-white/10 px-2.5 py-1 text-xs text-mute transition hover:border-white/30 hover:text-cream disabled:opacity-50"
            >
              ↺ Reset
            </button>
            <select
              value={language}
              onChange={(e) => onLanguageChange(e.target.value)}
              className="rounded-lg border border-white/10 bg-panelLight px-2.5 py-1 text-xs text-cream"
            >
              {LANGUAGES.map((l) => (
                <option key={l.id} value={l.id}>{l.label}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Monaco editor — fills remaining space */}
        <div className="flex-1 min-h-0">
          <Editor
            height="100%"
            theme="vs-dark"
            language={lang.monaco}
            value={code}
            onChange={(value) => setCode(value ?? "")}
            options={{
              fontSize: 13,
              minimap: { enabled: false },
              quickSuggestions: true,
              suggestOnTriggerCharacters: true,
              wordBasedSuggestions: "currentDocument",
              tabCompletion: "on",
              scrollBeyondLastLine: false,
            }}
          />
        </div>

        {/* Run button + results */}
        <div className="border-t border-white/5 px-4 py-3 shrink-0">
          <button
            onClick={onRun}
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

          {running && slowHint && (
            <p className="mt-2 text-xs text-white/50">
              Preparing a verified test environment — first run can take up to a minute.
            </p>
          )}

          {running && (
            <div className="mt-2 space-y-2">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-8 animate-pulse rounded-lg bg-white/5" />
              ))}
            </div>
          )}

          {!running && <TestResultsPanel testResults={testResults} revealedCount={revealedCount} />}
        </div>
      </div>
    </div>
  );
}
