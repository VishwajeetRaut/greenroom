/**
 * Palette data and placement geometry for the system-design board.
 *
 * Deliberately free of any @excalidraw/excalidraw import: this is the part
 * worth unit-testing, and pulling the editor bundle in just to check a label
 * list would make those tests depend on a canvas renderer. Element
 * construction (the one piece that genuinely needs the library) lives in
 * components/ArchitecturePalette.jsx.
 *
 * Why the labels are what they are
 * --------------------------------
 * A diagram is graded by serialising the board and asking the model which of
 * the question's `expected_components` are present (backend
 * services/llm.evaluate_diagram). The serialiser (useInterviewSession.js)
 * reads a shape's BOUND TEXT and falls back to the element type when there
 * isn't any — so an unlabelled box serialises as the literal string
 * "rectangle" and matches nothing. It contributes zero to the score no
 * matter how well placed it is.
 *
 * Every label below is copied verbatim from the `expected_components`
 * vocabulary used across the 20 system-design questions, ordered by how often
 * it appears there (load balancer 15x, cache 8x, message queue 8x, database
 * 7x, CDN 7x, app server 6x, object storage 4x). Inserting from this palette
 * means the diagram uses the exact names the grader looks for, rather than
 * "LB", "postgres", or an unlabelled rectangle.
 *
 * Shape choice is purely for readability — the serialiser treats rectangle,
 * ellipse and diamond identically.
 */
export const ARCHITECTURE_COMPONENTS = [
  { label: "client",               shape: "ellipse",   color: "#e9ecef" },
  { label: "load balancer",        shape: "diamond",   color: "#ffec99" },
  { label: "API gateway",          shape: "diamond",   color: "#ffec99" },
  { label: "app server",           shape: "rectangle", color: "#b2f2bb" },
  { label: "database",             shape: "ellipse",   color: "#a5d8ff" },
  { label: "cache",                shape: "rectangle", color: "#ffc9c9" },
  { label: "message queue",        shape: "rectangle", color: "#d0bfff" },
  { label: "CDN",                  shape: "ellipse",   color: "#99e9f2" },
  { label: "object storage",       shape: "rectangle", color: "#a5d8ff" },
  { label: "search index",         shape: "rectangle", color: "#eebefa" },
  { label: "worker pool",          shape: "rectangle", color: "#b2f2bb" },
  { label: "notification service", shape: "rectangle", color: "#ffd8a8" },
];

export const BOX_WIDTH = 170;
export const BOX_HEIGHT = 80;
export const GAP = 36;

function overlaps(element, x, y, width, height) {
  if (element.isDeleted) return false;
  const ex = element.x;
  const ey = element.y;
  const ew = element.width ?? 0;
  const eh = element.height ?? 0;
  return x < ex + ew + GAP && x + width + GAP > ex && y < ey + eh + GAP && y + height + GAP > ey;
}

/**
 * First grid slot in the visible viewport that doesn't collide with anything
 * already on the board.
 *
 * A fixed insertion point would stack every component on top of the last one,
 * and the scene origin would drop them off-screen the moment the candidate
 * scrolls — so placement is relative to the current viewport.
 */
export function findFreeSpot(appState, elements, width = BOX_WIDTH, height = BOX_HEIGHT) {
  const zoom = appState?.zoom?.value || 1;
  const originX = -(appState?.scrollX ?? 0) + GAP;
  const originY = -(appState?.scrollY ?? 0) + GAP;
  const viewWidth = (appState?.width || 900) / zoom;
  const viewHeight = (appState?.height || 600) / zoom;

  const stepX = width + GAP;
  const stepY = height + GAP;
  const columns = Math.max(1, Math.floor(viewWidth / stepX));
  const rows = Math.max(1, Math.floor(viewHeight / stepY));

  for (let row = 0; row < rows; row++) {
    for (let column = 0; column < columns; column++) {
      const x = originX + column * stepX;
      const y = originY + row * stepY;
      if (!elements.some((el) => overlaps(el, x, y, width, height))) return { x, y };
    }
  }
  // Viewport is full — cascade rather than refusing to insert, so a click
  // always does something visible.
  const offset = (elements.length % 8) * 24;
  return { x: originX + offset, y: originY + offset };
}

/** Excalidraw element skeleton for a palette component, ready for
 * convertToExcalidrawElements. Kept here so the shape of what gets inserted
 * is testable without loading the editor. */
export function componentSkeleton(component, x, y) {
  return {
    type: component.shape,
    x,
    y,
    width: BOX_WIDTH,
    height: BOX_HEIGHT,
    backgroundColor: component.color,
    fillStyle: "solid",
    strokeColor: "#1e1e1e",
    label: { text: component.label, fontSize: 16, strokeColor: "#1e1e1e" },
  };
}
