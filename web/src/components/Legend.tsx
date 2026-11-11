import {
  CHANGED_COLOR,
  LOCAL_VIEW_COLOR,
  BIRTH_COLOR,
  MATERIAL_COLOR,
  MESSAGE_COLOR,
  ORGANISM_COLOR,
  PACKED_COLOR,
  TRANSFER_COLOR,
  energyColor,
} from "../renderers/colors";

interface Props {
  energyCap: number;
  cellCount: number;
}

export default function Legend({ energyCap, cellCount }: Props) {
  return (
    <div className="grid-legend" aria-label="Color legend">
      <span className="legend-item">
        <i
          className="legend-ramp"
          style={{
            background: `linear-gradient(to right, ${energyColor(0, energyCap)}, ${energyColor(energyCap, energyCap)})`,
          }}
        />
        Energy 0 → {energyCap} (brighter = more)
      </span>
      <span className="legend-item">
        <i className="legend-swatch" style={{ background: MATERIAL_COLOR }} />
        Loose material (larger = more)
      </span>
      <span className="legend-item">
        <i className="legend-swatch" style={{ background: PACKED_COLOR }} />
        Packed material (larger = more; raises entry cost)
      </span>
      <span className="legend-item">
        <i className="legend-outline" style={{ borderColor: CHANGED_COLOR }} />
        Changed this tick
      </span>
      <span className="legend-item">
        <i className="legend-outline selected" />
        Selected
      </span>
      <span className="legend-item">
        <i className="legend-swatch" style={{ background: ORGANISM_COLOR }} />
        Living organism
      </span>
      <span className="legend-item">
        <i className="legend-swatch" style={{ background: LOCAL_VIEW_COLOR }} />
        Recorded local view
      </span>
      <span className="legend-item">
        <i className="legend-swatch" style={{ background: TRANSFER_COLOR }} />
        Gift (arrow)
      </span>
      <span className="legend-item">
        <i className="legend-swatch" style={{ background: MESSAGE_COLOR }} />
        Message (dashed arrow)
      </span>
      <span className="legend-item">
        <i className="legend-swatch" style={{ background: BIRTH_COLOR }} />
        Birth (dotted arrow)
      </span>
      <span className="grid-cell-count">
        {cellCount.toLocaleString("en-US")} cells
      </span>
    </div>
  );
}
