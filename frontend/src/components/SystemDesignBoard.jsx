import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Excalidraw } from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";

const AUTOSAVE_DEBOUNCE_MS = 2000;

// The scale numbers and tags come from the question's `scale_tiers` metadata,
// picked for this session's difficulty tier (see backend
// services/question_bank.scale_for). Before that metadata existed, the board
// showed nothing about the problem at all — the candidate had to hold the
// requirements in their head from what the interviewer said out loud, which
// is exactly the wrong thing to be spending working memory on during a
// system-design interview.
function RequirementsPanel({ questionContext }) {
  const [open, setOpen] = useState(true);
  if (!questionContext) return null;

  const { title, scale = [], tags = [], core_challenge: coreChallenge } = questionContext;
  const hasDetail = scale.length > 0 || tags.length > 0 || coreChallenge;
  if (!hasDetail && !title) return null;

  return (
    <div className="border-b border-white/5 bg-panelLight/30 px-5 py-3">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between text-left"
        aria-expanded={open}
      >
        <span className="text-sm font-medium text-cream">{title || "Requirements"}</span>
        <span className="text-xs text-mute">{open ? "Hide" : "Show"} requirements</span>
      </button>

      {open && hasDetail && (
        <div className="mt-3 space-y-3">
          {coreChallenge && <p className="text-xs leading-relaxed text-mute">{coreChallenge}</p>}

          {scale.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {scale.map((line) => (
                <span
                  key={line}
                  className="rounded-lg border border-white/10 bg-panel/60 px-2.5 py-1 text-xs text-cream"
                >
                  {line}
                </span>
              ))}
            </div>
          )}

          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {tags.map((tag) => (
                <span key={tag} className="rounded-full bg-coral/10 px-2 py-0.5 text-[11px] text-coral">
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const SystemDesignBoard = forwardRef(function SystemDesignBoard({ initialElements, onSave, questionContext }, ref) {
  const [api, setApi] = useState(null);
  const restoredRef = useRef(false);
  const saveTimerRef = useRef(null);

  useImperativeHandle(ref, () => ({
    getElements: () => api?.getSceneElements() ?? [],
  }));

  // Restore a previously-saved diagram once, as soon as both the Excalidraw
  // API and the resumed data are available (whichever arrives second).
  useEffect(() => {
    if (!api || restoredRef.current || !initialElements?.length) return;
    restoredRef.current = true;
    api.updateScene({ elements: initialElements });
  }, [api, initialElements]);

  const handleChange = useCallback((elements) => {
    if (!onSave) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => onSave(elements), AUTOSAVE_DEBOUNCE_MS);
  }, [onSave]);

  useEffect(() => () => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
  }, []);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-white/5 px-5 py-4">
        <div>
          <span className="text-sm text-cream">Architecture board</span>
          <p className="mt-0.5 text-xs text-mute">Your diagram is shared with the interviewer whether or not it&apos;s complete</p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-sage/15 px-3 py-1 text-xs text-sage">
          <span className="h-1.5 w-1.5 rounded-full bg-sage" />
          Live
        </span>
      </div>

      <RequirementsPanel questionContext={questionContext} />

      <div className="flex items-center gap-4 border-b border-white/5 bg-panelLight/30 px-5 py-2.5">
        <p className="text-xs text-mute">💡 Tips:</p>
        <p className="text-xs text-mute">Label your components clearly</p>
        <span className="text-white/10">·</span>
        <p className="text-xs text-mute">Use arrows to show data flow</p>
        <span className="text-white/10">·</span>
        <p className="text-xs text-mute">Think about scale &amp; failure points</p>
      </div>

      <div className="relative flex-1" style={{ minHeight: "480px" }}>
        <Excalidraw
          excalidrawAPI={(a) => setApi(a)}
          onChange={handleChange}
          theme="dark"
          UIOptions={{
            canvasActions: {
              export: { saveFileToDisk: false },
              loadScene: false,
              saveToActiveFile: false,
              toggleTheme: false,
            },
            tools: { image: false },
          }}
        />
      </div>
    </div>
  );
});

export default SystemDesignBoard;
