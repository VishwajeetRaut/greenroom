const STATUS_LABEL = {
  accepted: "Accepted",
  wrong_answer: "Wrong Answer",
  runtime_error: "Runtime Error",
  compile_error: "Compilation Error",
};

export default function TestResults({ results, revealedCount }) {
  if (!results) return null;

  const passed = results.status === "accepted";
  const visibleShown = results.visible_tests?.slice(0, revealedCount) ?? [];
  const hiddenRevealed = revealedCount > (results.visible_tests?.length ?? 0);

  return (
    <div className="mt-3 overflow-hidden rounded-lg border border-white/10 bg-ink">
      <div className={`flex items-center justify-between px-4 py-2 text-xs font-medium ${
        passed ? "bg-sage/15 text-sage" : "bg-coral/15 text-coral"
      }`}>
        <span>{STATUS_LABEL[results.status] ?? results.status}</span>
        <span className="text-mute">{results.passed} / {results.total} passed</span>
      </div>

      {results.compile_error && (
        <pre className="max-h-32 overflow-auto p-3 text-xs text-coral">
          {results.compile_error}
        </pre>
      )}

      {visibleShown.map((tc) => (
        <div
          key={tc.id}
          className={`border-t border-white/5 p-3 transition-all duration-300 ${tc.passed ? "" : "bg-coral/5"}`}
        >
          <div className={`flex items-center gap-2 text-xs font-medium ${tc.passed ? "text-sage" : "text-coral"}`}>
            <span>{tc.passed ? "✓" : "✗"}</span>
            <span>{tc.label}</span>
          </div>
          {!tc.passed && (
            <div className="mt-2 space-y-1 text-xs">
              <p className="text-mute">Input: <span className="font-mono text-cream">{tc.input}</span></p>
              <p className="text-mute">Expected: <span className="font-mono text-cream">{tc.expected}</span></p>
              <p className="text-mute">
                Got: <span className="font-mono text-coral">{tc.error ?? tc.output ?? "no output"}</span>
              </p>
            </div>
          )}
        </div>
      ))}

      {hiddenRevealed && results.hidden_tests?.length > 0 && (
        <div className="border-t border-white/5 p-3">
          <p className="mb-2 text-xs text-mute">Hidden test cases</p>
          <div className="flex flex-wrap gap-2">
            {results.hidden_tests.map((tc) => (
              <span
                key={tc.id}
                className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs ${
                  tc.passed ? "bg-sage/15 text-sage" : "bg-coral/15 text-coral"
                }`}
              >
                🔒 {tc.passed ? "✓" : "✗"}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
