import { convertToExcalidrawElements } from "@excalidraw/excalidraw";

import {
  ARCHITECTURE_COMPONENTS,
  componentSkeleton,
  findFreeSpot,
} from "../lib/architectureComponents";

/**
 * One-click, pre-labelled architecture components for the system-design board.
 *
 * The labels are not cosmetic — see lib/architectureComponents.js for why they
 * are copied verbatim from the backend's `expected_components` vocabulary.
 */
export default function ArchitecturePalette({ api, disabled }) {
  const insert = (component) => {
    if (!api) return;
    const existing = api.getSceneElements();
    const { x, y } = findFreeSpot(api.getAppState(), existing);
    // Two elements come back: the shape and the text bound to it. Both must
    // go into the scene — the serialiser reads a component's name off the
    // bound text, so dropping it would insert a box that grades as
    // "rectangle".
    const created = convertToExcalidrawElements([componentSkeleton(component, x, y)]);
    const container = created.find((el) => el.type === component.shape) ?? created[0];
    api.updateScene({
      elements: [...existing, ...created],
      // Select what was just added so it can be dragged immediately — without
      // this the candidate has to hunt for it before they can position it.
      appState: { selectedElementIds: { [container.id]: true } },
    });
  };

  return (
    <div className="border-b border-white/5 bg-panelLight/30 px-5 py-2.5">
      <div className="flex items-start gap-3">
        <p className="shrink-0 pt-1 text-xs text-mute">🧩 Components:</p>
        <div className="flex flex-wrap gap-1.5">
          {ARCHITECTURE_COMPONENTS.map((component) => (
            <button
              key={component.label}
              type="button"
              disabled={disabled || !api}
              onClick={() => insert(component)}
              title={`Add a labelled "${component.label}" box to the board`}
              className="rounded-lg border border-white/10 bg-panel/60 px-2.5 py-1 text-xs capitalize text-cream transition hover:border-white/30 hover:bg-panel disabled:cursor-not-allowed disabled:opacity-40"
            >
              <span
                aria-hidden="true"
                className="mr-1.5 inline-block h-2 w-2 rounded-[2px] align-middle"
                style={{ backgroundColor: component.color }}
              />
              {component.label}
            </button>
          ))}
        </div>
      </div>
      <p className="mt-1.5 text-[11px] text-mute">
        Inserted boxes come pre-labelled — the interviewer reads those labels when scoring your diagram.
      </p>
    </div>
  );
}
