import { forwardRef, useImperativeHandle, useState } from "react";
import { Excalidraw } from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";

const SystemDesignBoard = forwardRef(function SystemDesignBoard(_props, ref) {
  const [api, setApi] = useState(null);

  useImperativeHandle(ref, () => ({
    getElements: () => api?.getSceneElements() ?? [],
  }));

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
