import { useRef, useState } from "react";
import { api } from "../lib/api";
import { LANGUAGES } from "../components/CodeEditor";

export const STARTER_CODE = {
  python: `from collections import defaultdict, Counter, deque
import heapq
from typing import List, Optional, Tuple

# Write your solution here
`,
  javascript: `// Write your solution here
`,
  java: `import java.util.*;
import java.util.stream.*;

class Solution {
    // Write your solution here
}
`,
  cpp: `#include <bits/stdc++.h>
using namespace std;

// Write your solution here
`,
};

export function useCodeRunner() {
  const [language, setLanguage] = useState("python");
  const [code, setCode] = useState(STARTER_CODE.python);
  const [testResults, setTestResults] = useState(null);
  const [revealedCount, setRevealedCount] = useState(0);
  const [running, setRunning] = useState(false);
  const [slowHint, setSlowHint] = useState(false);
  const [boilerplateNote, setBoilerplateNote] = useState(null);
  const [questionContext, setQuestionContext] = useState(null);
  const boilerplateRequestRef = useRef(0);
  // Tracks the last-known-original source per language, so the reset button
  // can restore it without refetching (starter code, or fetched harness boilerplate).
  const originalCodeRef = useRef({ python: STARTER_CODE.python });

  // Fetches question-specific boilerplate for `langId` and swaps it in if found.
  // Shared by language switching and by the question-assigned handler below,
  // since Python is the default language and never goes through a "switch".
  const fetchBoilerplate = async (langId, sessionId) => {
    const lang = LANGUAGES.find((l) => l.id === langId);
    if (!sessionId) return;

    const requestId = ++boilerplateRequestRef.current;
    try {
      const res = await api.getBoilerplate(sessionId, lang.piston);
      if (boilerplateRequestRef.current !== requestId) return;
      if (res.boilerplate) {
        setCode(res.boilerplate);
        originalCodeRef.current[langId] = res.boilerplate;
      }
      // If !res.supported, silently keep the default starter code.
      // The test runner will show a specific error if the user tries to run.
    } catch {
      // Generic starter code already showing — silently keep it.
    }
  };

  // sessionId is passed per-call so the hook doesn't need it at construction time
  const handleLanguageChange = async (newLanguage, sessionId) => {
    setLanguage(newLanguage);
    setCode(STARTER_CODE[newLanguage]);
    originalCodeRef.current[newLanguage] = STARTER_CODE[newLanguage];
    setTestResults(null);
    setRevealedCount(0);
    setBoilerplateNote(null);
    await fetchBoilerplate(newLanguage, sessionId);
  };

  // Called once the interviewer assigns a technical question — fetches
  // boilerplate for whatever language is currently selected (usually the
  // default, Python, which never goes through handleLanguageChange).
  const handleQuestionAssigned = (ctx, sessionId) => {
    setQuestionContext(ctx);
    fetchBoilerplate(language, sessionId);
  };

  const handleResetBoilerplate = () => {
    const original = originalCodeRef.current[language];
    if (original === undefined) return;
    setCode(original);
    setTestResults(null);
    setRevealedCount(0);
  };

  const handleRunCode = async (sessionId) => {
    if (!sessionId) return;
    const lang = LANGUAGES.find((l) => l.id === language);
    api.trackEvent("code_run", { sessionId, properties: { language: lang.id } });
    setRunning(true);
    setTestResults(null);
    setRevealedCount(0);
    setSlowHint(false);

    const slowHintTimer =
      lang.id === "java" || lang.id === "cpp" ? setTimeout(() => setSlowHint(true), 5000) : null;

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
      if (slowHintTimer) clearTimeout(slowHintTimer);
      setSlowHint(false);
      setRunning(false);
    }
  };

  return {
    language,
    code,
    setCode,
    testResults,
    revealedCount,
    running,
    slowHint,
    boilerplateNote,
    questionContext,
    setQuestionContext,
    handleLanguageChange,
    handleQuestionAssigned,
    handleRunCode,
    handleResetBoilerplate,
  };
}
