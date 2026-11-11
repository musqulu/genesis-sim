import { useLayoutEffect, useRef, useState } from "react";
import type { Cell, Interaction, WorldView } from "../api";
import {
  CHANGED_COLOR,
  LOCAL_VIEW_COLOR,
  BIRTH_COLOR,
  MATERIAL_COLOR,
  MESSAGE_COLOR,
  ORGANISM_COLOR,
  PACKED_COLOR,
  SELECTED_COLOR,
  TRANSFER_COLOR,
  energyColor,
} from "./colors";

/** Ticks over which an exchange arrow fades (display only). */
export const EXCHANGE_FADE = 8;

export function cellFromPoint(
  clientX: number,
  clientY: number,
  rect: Pick<DOMRect, "left" | "top" | "width" | "height">,
  width: number,
  height: number,
): Cell | null {
  if (rect.width <= 0 || rect.height <= 0) return null;
  const x = Math.floor(((clientX - rect.left) / rect.width) * width);
  const y = Math.floor(((clientY - rect.top) / rect.height) * height);
  return x >= 0 && x < width && y >= 0 && y < height ? { x, y } : null;
}

interface Props {
  frame: WorldView;
  selected: Cell;
  highlight: ReadonlySet<number>;
  onSelect: (cell: Cell) => void;
  /** Cells in the selected organism's recorded observation (display only). */
  localView?: ReadonlySet<number>;
  selectedOrganism?: string | null;
  /** Recent transfers/messages to draw, with their age in ticks (display only). */
  interactions?: (Interaction & { age: number })[];
  /** Living organism under the pointer (display only), with pointer coordinates. */
  onHoverOrganism?: (
    id: string | null,
    clientX: number,
    clientY: number,
  ) => void;
}

const NONE: ReadonlySet<number> = new Set();

const MATERIAL_SCALE = 10;

export default function CanvasGrid({
  frame,
  selected,
  highlight,
  onSelect,
  localView = NONE,
  selectedOrganism = null,
  interactions = [],
  onHoverOrganism,
}: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [hover, setHover] = useState<Cell | null>(null);
  const { width, height, energy_cap: cap } = frame.config;

  useLayoutEffect(() => {
    const canvas = ref.current!;
    const draw = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(rect.width * dpr);
      canvas.height = Math.round(rect.height * dpr);
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = "#101612";
      ctx.fillRect(0, 0, rect.width, rect.height);
      const cw = rect.width / width;
      const ch = rect.height / height;
      frame.cells.forEach((cell, index) => {
        const left = cell.x * cw + 0.6;
        const top = cell.y * ch + 0.6;
        ctx.fillStyle = energyColor(cell.energy, cap);
        ctx.fillRect(left, top, cw - 1.2, ch - 1.2);
        if (cell.loose_material > 0) {
          const size =
            Math.min(1, 0.25 + cell.loose_material / MATERIAL_SCALE) * 0.5;
          ctx.fillStyle = MATERIAL_COLOR;
          ctx.fillRect(
            left + (cw - 1.2) * (1 - size),
            top + (ch - 1.2) * (1 - size),
            (cw - 1.2) * size,
            (ch - 1.2) * size,
          );
        }
        if (cell.packed_material > 0) {
          // Grows with packed units (cap from config); packed cells cost more to enter.
          const share =
            0.3 +
            0.5 *
              Math.min(
                1,
                cell.packed_material / Math.max(1, frame.config.packed_cap),
              );
          ctx.fillStyle = PACKED_COLOR;
          ctx.fillRect(left, top, (cw - 1.2) * share, (ch - 1.2) * share);
        }
        if (highlight.has(index)) {
          ctx.strokeStyle = CHANGED_COLOR;
          ctx.lineWidth = 2;
          ctx.strokeRect(left + 1, top + 1, cw - 3.2, ch - 3.2);
        }
      });
      for (const index of localView) {
        const x = index % width;
        const y = Math.floor(index / width);
        ctx.fillStyle = LOCAL_VIEW_COLOR;
        ctx.fillRect(x * cw + 0.6, y * ch + 0.6, cw - 1.2, ch - 1.2);
      }
      for (const organism of frame.organisms) {
        if (!organism.alive) continue; // the dead leave no mark on the grid
        const inset = 0.25;
        ctx.fillStyle = ORGANISM_COLOR;
        ctx.fillRect(
          (organism.x + inset) * cw,
          (organism.y + inset) * ch,
          cw * (1 - 2 * inset),
          ch * (1 - 2 * inset),
        );
        if (organism.id === selectedOrganism) {
          ctx.strokeStyle = ORGANISM_COLOR;
          ctx.lineWidth = 2;
          ctx.strokeRect(
            organism.x * cw - 3,
            organism.y * ch - 3,
            cw + 6,
            ch + 6,
          );
        }
      }
      for (const line of interactions) {
        // Newest is opaque; older exchanges fade over EXCHANGE_FADE ticks.
        const alpha = Math.max(0.15, 1 - line.age / EXCHANGE_FADE);
        const ax = (line.from_x + 0.5) * cw;
        const ay = (line.from_y + 0.5) * ch;
        const bx = (line.to_x + 0.5) * cw;
        const by = (line.to_y + 0.5) * ch;
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = ctx.fillStyle =
          line.kind === "transfer"
            ? TRANSFER_COLOR
            : line.kind === "reproduce"
              ? BIRTH_COLOR
              : MESSAGE_COLOR;
        ctx.lineWidth = Math.max(2, Math.min(cw, ch) * 0.14);
        ctx.setLineDash(
          line.kind === "transfer"
            ? []
            : line.kind === "reproduce"
              ? [1, 3]
              : [4, 3],
        );
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
        ctx.stroke();
        const angle = Math.atan2(by - ay, bx - ax);
        const head = Math.min(cw, ch) * 0.35;
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(bx, by);
        ctx.lineTo(
          bx - head * Math.cos(angle - 0.5),
          by - head * Math.sin(angle - 0.5),
        );
        ctx.lineTo(
          bx - head * Math.cos(angle + 0.5),
          by - head * Math.sin(angle + 0.5),
        );
        ctx.closePath();
        ctx.fill();
        ctx.restore();
      }
      if (hover) {
        ctx.strokeStyle = "#8fa68a";
        ctx.lineWidth = 1;
        ctx.strokeRect(hover.x * cw + 0.5, hover.y * ch + 0.5, cw - 1, ch - 1);
      }
      ctx.strokeStyle = SELECTED_COLOR;
      ctx.lineWidth = 2;
      ctx.strokeRect(selected.x * cw - 1, selected.y * ch - 1, cw + 2, ch + 2);
    };
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    window.addEventListener("resize", draw);
    draw();
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", draw);
    };
  }, [
    frame,
    selected,
    hover,
    highlight,
    localView,
    selectedOrganism,
    interactions,
    width,
    height,
    cap,
  ]);

  return (
    <canvas
      ref={ref}
      className="world-canvas"
      style={{ aspectRatio: `${width} / ${height}` }}
      tabIndex={0}
      role="img"
      aria-label={`World grid, ${width} columns by ${height} rows. Brightness shows energy. Selected cell ${selected.x}, ${selected.y}. Use arrow keys to select a cell.`}
      aria-describedby="grid-help"
      onPointerMove={(event) => {
        const cell = cellFromPoint(
          event.clientX,
          event.clientY,
          event.currentTarget.getBoundingClientRect(),
          width,
          height,
        );
        setHover(cell);
        if (onHoverOrganism) {
          const organism = cell
            ? frame.organisms.find(
                (o) => o.alive && o.x === cell.x && o.y === cell.y,
              )
            : undefined;
          onHoverOrganism(organism?.id ?? null, event.clientX, event.clientY);
        }
      }}
      onPointerLeave={() => {
        setHover(null);
        onHoverOrganism?.(null, 0, 0);
      }}
      onClick={(event) => {
        const cell = cellFromPoint(
          event.clientX,
          event.clientY,
          event.currentTarget.getBoundingClientRect(),
          width,
          height,
        );
        if (cell) onSelect(cell);
        event.currentTarget.focus();
      }}
      onKeyDown={(event) => {
        const directions: Record<string, Cell> = {
          ArrowLeft: { x: -1, y: 0 },
          ArrowRight: { x: 1, y: 0 },
          ArrowUp: { x: 0, y: -1 },
          ArrowDown: { x: 0, y: 1 },
        };
        const delta = directions[event.key];
        if (delta) {
          event.preventDefault();
          onSelect({
            x: Math.max(0, Math.min(width - 1, selected.x + delta.x)),
            y: Math.max(0, Math.min(height - 1, selected.y + delta.y)),
          });
        }
      }}
    >
      Use the coordinate inputs in the cell inspector to select a cell.
    </canvas>
  );
}
