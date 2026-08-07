/**
 * The architecture palette exists to make diagrams gradable, so these tests
 * pin the two things that determine whether it works:
 *
 *   1. Its labels are drawn from the vocabulary the backend actually grades
 *      against (`expected_components` in the question bank). A palette that
 *      inserts "LB" instead of "load balancer" is worse than no palette —
 *      it makes candidates confident about a box that scores nothing.
 *   2. Inserted components don't stack on top of each other.
 *
 * The vocabulary check reads the real question bank, so drifting the palette
 * away from it (or renaming a component in the bank) fails here.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  ARCHITECTURE_COMPONENTS,
  componentSkeleton,
  findFreeSpot,
} from "../lib/architectureComponents.js";

const here = dirname(fileURLToPath(import.meta.url));
const bankPath = resolve(here, "../../../backend/data/question_bank.json");

function expectedComponentVocabulary() {
  const bank = JSON.parse(readFileSync(bankPath, "utf-8"));
  const vocabulary = new Set();
  for (const question of bank) {
    if (question.track !== "system-design") continue;
    for (const component of question.expected_components || []) {
      vocabulary.add(component.toLowerCase());
    }
  }
  return vocabulary;
}

// The only palette entry with no expected_components backing: nothing grades
// a client, but nearly every design starts from one and arrows need an origin.
const UNGRADED_BY_DESIGN = new Set(["client"]);

describe("palette labels match the grading vocabulary", () => {
  it("every label is a real expected_component (or a documented exception)", () => {
    const vocabulary = expectedComponentVocabulary();
    const unknown = ARCHITECTURE_COMPONENTS
      .map((c) => c.label.toLowerCase())
      .filter((label) => !vocabulary.has(label) && !UNGRADED_BY_DESIGN.has(label));
    expect(unknown).toEqual([]);
  });

  it("covers the components that appear most often across the question bank", () => {
    const bank = JSON.parse(readFileSync(bankPath, "utf-8"));
    const counts = new Map();
    for (const question of bank) {
      if (question.track !== "system-design") continue;
      for (const component of question.expected_components || []) {
        const key = component.toLowerCase();
        counts.set(key, (counts.get(key) || 0) + 1);
      }
    }
    const topSeven = [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 7)
      .map(([label]) => label);

    const palette = new Set(ARCHITECTURE_COMPONENTS.map((c) => c.label.toLowerCase()));
    const missing = topSeven.filter((label) => !palette.has(label));
    expect(missing).toEqual([]);
  });

  it("has no duplicate labels", () => {
    const labels = ARCHITECTURE_COMPONENTS.map((c) => c.label);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("puts the label where the serialiser reads it", () => {
    // The serialiser resolves a shape's text via its bound label; a skeleton
    // without one would insert a box that grades as "rectangle".
    for (const component of ARCHITECTURE_COMPONENTS) {
      const skeleton = componentSkeleton(component, 0, 0);
      expect(skeleton.label?.text).toBe(component.label);
    }
  });

  it("only uses shapes the diagram serialiser recognises as components", () => {
    // useInterviewSession.js counts rectangle/ellipse/diamond (among others)
    // as components; anything else would be dropped from the serialisation.
    const recognised = new Set(["rectangle", "ellipse", "diamond"]);
    for (const component of ARCHITECTURE_COMPONENTS) {
      expect(recognised.has(component.shape)).toBe(true);
    }
  });
});

describe("findFreeSpot", () => {
  const appState = { scrollX: 0, scrollY: 0, zoom: { value: 1 }, width: 900, height: 600 };

  it("places the first component inside the visible viewport", () => {
    const { x, y } = findFreeSpot(appState, []);
    expect(x).toBeGreaterThanOrEqual(0);
    expect(y).toBeGreaterThanOrEqual(0);
    expect(x).toBeLessThan(900);
    expect(y).toBeLessThan(600);
  });

  it("does not place a component on top of an existing one", () => {
    const first = findFreeSpot(appState, []);
    const existing = [{ x: first.x, y: first.y, width: 170, height: 80 }];
    const second = findFreeSpot(appState, existing);
    expect(second).not.toEqual(first);
  });

  it("keeps finding fresh slots as the board fills up", () => {
    const placed = [];
    const seen = new Set();
    for (let i = 0; i < 6; i++) {
      const spot = findFreeSpot(appState, placed);
      const key = `${spot.x},${spot.y}`;
      expect(seen.has(key)).toBe(false);
      seen.add(key);
      placed.push({ ...spot, width: 170, height: 80 });
    }
  });

  it("follows the viewport when the canvas is scrolled", () => {
    const scrolled = { ...appState, scrollX: -1000, scrollY: -500 };
    const { x, y } = findFreeSpot(scrolled, []);
    // scene coords of the viewport's top-left are (-scrollX, -scrollY)
    expect(x).toBeGreaterThanOrEqual(1000);
    expect(y).toBeGreaterThanOrEqual(500);
  });

  it("still returns a visible position when the viewport is full", () => {
    // A wall of overlapping elements covering the whole viewport.
    const wall = [];
    for (let x = 0; x < 1200; x += 40) {
      for (let y = 0; y < 800; y += 40) wall.push({ x, y, width: 200, height: 120 });
    }
    const spot = findFreeSpot(appState, wall);
    expect(Number.isFinite(spot.x)).toBe(true);
    expect(Number.isFinite(spot.y)).toBe(true);
    expect(spot.x).toBeLessThan(900);
  });

  it("ignores deleted elements when looking for space", () => {
    const first = findFreeSpot(appState, []);
    const deleted = [{ x: first.x, y: first.y, width: 170, height: 80, isDeleted: true }];
    expect(findFreeSpot(appState, deleted)).toEqual(first);
  });
});
