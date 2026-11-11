import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import CanvasGrid, { cellFromPoint } from "./CanvasGrid";
import type { Cell } from "../api";
import { makeFrame } from "../testFixtures";
import {
  CHANGED_COLOR,
  LOCAL_VIEW_COLOR,
  ORGANISM_COLOR,
  energyColor,
} from "./colors";
import { makeOrganism } from "../testFixtures";

const frame = makeFrame();
const none: ReadonlySet<number> = new Set();

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    setTransform: vi.fn(),
    fillRect: vi.fn(),
    strokeRect: vi.fn(),
  } as unknown as CanvasRenderingContext2D);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("coordinate mapping", () => {
  it("uses CSS geometry rather than backing pixels across resize and offsets", () => {
    for (const size of [288, 512, 640]) {
      const rect = { left: 37, top: 103, width: size, height: size };
      expect(
        cellFromPoint(37 + size / 64, 103 + size / 64, rect, 32, 32),
      ).toEqual({ x: 0, y: 0 });
      expect(
        cellFromPoint(37 + size - 1, 103 + size - 1, rect, 32, 32),
      ).toEqual({ x: 31, y: 31 });
      expect(
        cellFromPoint(
          37 + (size * 11.5) / 32,
          103 + (size * 23.5) / 32,
          rect,
          32,
          32,
        ),
      ).toEqual({ x: 11, y: 23 });
      expect(cellFromPoint(37 + size, 103, rect, 32, 32)).toBeNull();
      expect(cellFromPoint(36, 103, rect, 32, 32)).toBeNull();
    }
  });
  it("rejects zero-sized canvas geometry", () => {
    expect(
      cellFromPoint(0, 0, { left: 0, top: 0, width: 0, height: 0 }, 32, 32),
    ).toBeNull();
  });
});

it("click and keyboard selection survive a changed canvas size without mutating the frame", () => {
  const before = JSON.stringify(frame);
  function Harness() {
    const [selected, setSelected] = useState<Cell>({ x: 0, y: 0 });
    return (
      <>
        <CanvasGrid
          frame={frame}
          selected={selected}
          highlight={none}
          onSelect={setSelected}
        />
        <output>
          {selected.x},{selected.y}
        </output>
      </>
    );
  }
  render(<Harness />);
  const canvas = screen.getByRole("img") as HTMLCanvasElement;
  const rect = vi.spyOn(canvas, "getBoundingClientRect");
  rect.mockReturnValue({
    left: 10,
    top: 20,
    width: 640,
    height: 640,
  } as DOMRect);
  fireEvent.click(canvas, { clientX: 649, clientY: 659 });
  expect(screen.getByRole("status").textContent).toBe("31,31");
  rect.mockReturnValue({
    left: 10,
    top: 20,
    width: 320,
    height: 320,
  } as DOMRect);
  fireEvent(window, new Event("resize"));
  fireEvent.click(canvas, { clientX: 125, clientY: 255 });
  expect(screen.getByRole("status").textContent).toBe("11,23");
  fireEvent.keyDown(canvas, { key: "ArrowRight" });
  expect(screen.getByRole("status").textContent).toBe("12,23");
  fireEvent.click(canvas, { clientX: 11, clientY: 21 });
  fireEvent.keyDown(canvas, { key: "ArrowLeft" });
  expect(screen.getByRole("status").textContent).toBe("0,0");
  expect(JSON.stringify(frame)).toBe(before);
});

it("draws energy as brightness and outlines only highlighted cells", () => {
  const fills: string[] = [];
  const strokes: string[] = [];
  const ctx = {
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    setTransform: vi.fn(),
    fillRect(this: { fillStyle: string }) {
      fills.push(this.fillStyle);
    },
    strokeRect(this: { strokeStyle: string }) {
      strokes.push(this.strokeStyle);
    },
  };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    ctx as unknown as CanvasRenderingContext2D,
  );
  vi.spyOn(
    HTMLCanvasElement.prototype,
    "getBoundingClientRect",
  ).mockReturnValue({ left: 0, top: 0, width: 320, height: 320 } as DOMRect);
  render(
    <CanvasGrid
      frame={frame}
      selected={{ x: 0, y: 0 }}
      highlight={new Set([5, 6])}
      onSelect={() => undefined}
    />,
  );
  expect(fills).toContain(energyColor(frame.cells[1].energy, 100));
  expect(energyColor(0, 100)).not.toBe(energyColor(100, 100));
  expect(strokes.filter((color) => color === CHANGED_COLOR)).toHaveLength(2);
});

it("draws living organisms, never the dead, and the recorded local view", () => {
  const fills: string[] = [];
  const ctx = {
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    setTransform: vi.fn(),
    fillRect(this: { fillStyle: string }) {
      fills.push(this.fillStyle);
    },
    strokeRect: vi.fn(),
  };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    ctx as unknown as CanvasRenderingContext2D,
  );
  const withLife = {
    ...frame,
    organisms: [
      makeOrganism(),
      makeOrganism({
        id: "org-0002",
        alive: false,
        energy: 0,
        death_tick: 2,
        death_cause: "age",
      }),
    ],
  };
  render(
    <CanvasGrid
      frame={withLife}
      selected={{ x: 0, y: 0 }}
      highlight={none}
      localView={new Set([0, 1, 32])}
      selectedOrganism="org-0001"
      onSelect={() => undefined}
    />,
  );
  expect(fills.filter((c) => c === ORGANISM_COLOR)).toHaveLength(1);
  expect(fills.filter((c) => c === LOCAL_VIEW_COLOR)).toHaveLength(3);
});
