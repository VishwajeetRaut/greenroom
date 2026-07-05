import { useRef, useState } from "react";
import Editor from "@monaco-editor/react";
import { api } from "../lib/api";
import TestResults from "./TestResults";

const LANGUAGES = [
  { id: "python",     label: "Python",     monaco: "python",     piston: "python", version: "3.10.0"  },
  { id: "javascript", label: "JavaScript", monaco: "javascript", piston: "node",   version: "18.15.0" },
  { id: "java",       label: "Java",       monaco: "java",       piston: "java",   version: "15.0.2"  },
  { id: "cpp",        label: "C++",        monaco: "cpp",        piston: "gcc",    version: "10.2.0"  },
];

const STARTER_CODE = {
  python: `# Write your solution here\n`,
  javascript: `// Write your solution here\n`,
  java: `class Solution {\n    // Write your solution here\n}\n`,
  cpp: `#include <bits/stdc++.h>\nusing namespace std;\n\n// Write your solution here\n`,
};

export { LANGUAGES, STARTER_CODE };

export default function CodeEditor({ sessionId, language, code, onLanguageChange, onCodeChange }) {
  const [running, setRunning] = useState(false);
  const [slowHint, setSlowHint] = useState(false);
  const [testResults, setTestResults] = useState(null);
  const [revealedCount, setRevealedCount] = useState(0);
  const [boilerplateNote, setBoilerplateNote] = useState(null);
  const boilerplateReqRef = useRef(0);

  const handleLanguageChange = async (newLang) => {
    onLanguageChange(newLang);
    onCodeChange(STARTER_CODE[newLang]);
    setTestResults(null);
    setRevealedCount(0);
    setBoilerplateNote(null);

    const lang = LANGUAGES.find((l) => l.id === newLang);
    if (!sessionId || (lang.id !== "java" && lang.id !== "cpp")) return;

    // Java/C++ need a generated harness-specific boilerplate — the generic
    // starter code above has the wrong method signature for most problems.
    const reqId = ++boilerplateReqRef.current;
    try {
      const res = await api.getBoilerplate(sessionId, lang.piston);
      if (boilerplateReqRef.current !== reqId) return;
      if (res.boilerplate) {
        onCodeChange(res.boilerplate);
      } else if (!res.supported) {
        setBoilerplateNote(`This problem doesn't support ${lang.label} yet — try Python or JavaScript.`);
      }
    } catch {
      // Keep generic starter code — silently acceptable.
    }
  };

  const handleRun = async () => {
    const lang = LANGUAGES.find((l) => l.id === language);
    setRunning(true);
    setTestResults(null);
    setRevealedCount(0);
    setSlowHint(false);

    // First run for a new Java/C++ problem generates a verified harness — can
    // take up to a minute. Show a hint after 5s so it doesn't look like a hang.
    const hintTimer =
      lang.id === "java" || lang.id === "cpp"
        ? setTimeout(() => setSlowHint(true), 5000)
        : null;

    try {
      const res = await api.runTests({
        session_id: sessionId,
        language: lang.piston,
        version: lang.version,
        source: code,
      });
      setTestResults(res);

      const visibleLen = res.visible_tests?.length ?? 0;
      const hiddenLen  = res.hidden_tests?.length ?? 0;
      const total = visibleLen + (hiddenLen > 0 ? 1 : 0);
      for (let i = 1; i <= total; i++) {
        setTimeout(() => setRevealedCount(i), i * 300);
      }
    } catch {
      setTestResults({
        status: "compile_error",
        compile_error: "Could not reach the code execution service.",
        visible_tests: [], hidden_tests: [], passed: 0, total: 0,
      });
    } finally {
      if (hintTimer) clearTimeout(hintTimer);
      setSlowHint(false);
      setRunning(false);
    }
  };

  const currentLang = LANGUAGES.find((l) => l.id === language);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-white/5 px-5 py-4">
        <span className="text-sm text-mute">Code editor</span>
        <select
          value={language}
          onChange={(e) => handleLanguageChange(e.target.value)}
          className="rounded-lg border border-white/10 bg-panelLight px-3 py-1.5 text-xs text-cream"
        >
          {LANGUAGES.map((l) => (
            <option key={l.id} value={l.id}>{l.label}</option>
          ))}
        </select>
      </div>

      {boilerplateNote && (
        <p className="border-b border-white/5 px-5 py-2 text-xs text-amber-300/80">{boilerplateNote}</p>
      )}

      <div className="flex-1">
        <Editor
          height="320px"
          theme="vs-dark"
          language={currentLang.monaco}
          value={code}
          onChange={(value) => onCodeChange(value ?? "")}
          options={{ fontSize: 13, minimap: { enabled: false } }}
        />
      </div>

      <div className="border-t border-white/5 p-4">
        <button
          onClick={handleRun}
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
          <p className="mt-3 text-xs text-white/50">
            Preparing a verified test environment for this language — first run on a new problem
            can take up to a minute. Future runs will be instant.
          </p>
        )}

        {running && !testResults && (
          <div className="mt-3 space-y-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-8 animate-pulse rounded-lg bg-white/5" />
            ))}
          </div>
        )}

        {!running && <TestResults results={testResults} revealedCount={revealedCount} />}
      </div>
    </div>
  );
}
