/** Display-only color mapping; nothing here feeds back into the simulation. */

const DARK = [27, 36, 30];
const BRIGHT = [168, 232, 120];

export function energyColor(energy: number, cap: number): string {
  const t = cap > 0 ? Math.max(0, Math.min(1, energy / cap)) : 0;
  const [r, g, b] = DARK.map((low, i) =>
    Math.round(low + (BRIGHT[i] - low) * t),
  );
  return `rgb(${r}, ${g}, ${b})`;
}

export const MATERIAL_COLOR = "#e0a458";
export const PACKED_COLOR = "#8c93a3";
export const CHANGED_COLOR = "#69d2ff";
export const SELECTED_COLOR = "#ffffff";
export const ORGANISM_COLOR = "#ff8fd1";
export const LOCAL_VIEW_COLOR = "rgba(255, 143, 209, 0.28)";
export const TRANSFER_COLOR = "#ffd166"; // a gift of energy or material (solid arrow)
export const MESSAGE_COLOR = "#7cc4ff"; // a message (dashed arrow)
export const BIRTH_COLOR = "#ff8fd1"; // a birth: parent to child (dotted arrow)
