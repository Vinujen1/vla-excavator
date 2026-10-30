import { fmtNum, fmtTime } from "../api";
import type { DepthEstimate, Target } from "../types";
import { Badge, Button, Card, Note, Row } from "../ui";

export function depthTone(d: DepthEstimate) {
  return d.status === "ok" ? "ok" : d.status === "inconsistent" ? "warn" : "fault";
}

export function depthText(d: DepthEstimate) {
  if (d.status === "ok") return `${d.depth_m!.toFixed(3)} m`;
  if (d.status === "inconsistent") return `${d.depth_m!.toFixed(3)} m ?`;
  return d.status === "no_valid_depth" ? "no valid depth" : "unavailable";
}

export function TargetPanel({ target, onClear }: { target: Target | null; onClear: () => void }) {
  if (!target) {
    return (
      <Card title="Selected target">
        <p className="text-sm text-muted">No target selected.</p>
        <p className="mt-1 text-xs text-faint">Click a box in the camera view, or choose an object from the scene list.</p>
      </Card>
    );
  }
  const d = target.detection;
  const dep = d.depth;
  const cx = Math.round((d.bbox.x1 + d.bbox.x2) / 2);
  const cy = Math.round((d.bbox.y1 + d.bbox.y2) / 2);

  return (
    <Card title="Selected target"
      right={target.stale ? <Badge tone="warn">Stale</Badge> : <Badge tone="ok" pulse>Current</Badge>}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="text-lg font-semibold text-graphite">{d.label}</div>
          <div className="text-xs text-muted">
            {d.source === "yolo" ? "YOLO detection" : "Colour-marker candidate (colour threshold only)"} · id {d.id}
          </div>
        </div>
        <Button onClick={onClear}>Clear</Button>
      </div>

      {target.stale && <div className="mb-3"><Note tone="warn">Stale: {target.stale_reason}. Values show the last matched observation.</Note></div>}

      <div className="mb-3 rounded-lg border border-rule bg-paper p-3">
        <div className="text-[12px] text-muted">Target depth (aligned, median of ROI)</div>
        <div className={`font-display text-4xl font-semibold tabular-nums ${dep.status === "ok" ? "text-accent" : dep.status === "inconsistent" ? "text-warn" : "text-fault"}`}>
          {depthText(dep)}
        </div>
        <div className="mt-1 text-xs text-muted">
          spread (IQR) {fmtNum(dep.spread_m, 3, " m")} · valid pixels {(dep.valid_fraction * 100).toFixed(0)}%
        </div>
        {dep.reason && <div className="mt-1 text-xs text-warn">{dep.reason}</div>}
      </div>

      <Row label="Confidence">{d.confidence !== null ? `${(d.confidence * 100).toFixed(1)} %` : "n/a (colour threshold)"}</Row>
      <Row label="Box centre (px)">{cx}, {cy}</Row>
      <Row label="Box (x1,y1,x2,y2)">{d.bbox.x1}, {d.bbox.y1}, {d.bbox.x2}, {d.bbox.y2}</Row>
      <Row label="XYZ camera frame">
        {dep.xyz_camera_m ? dep.xyz_camera_m.map((v) => v.toFixed(3)).join(", ") + " m" : "—"}
      </Row>
      <Row label="Last seen">{fmtTime(target.last_seen_time)} (frame {target.last_seen_frame_number})</Row>
      <Row label="Age">{target.age_s.toFixed(2)} s</Row>
      <Row label="Selected">{fmtTime(target.selected_at)}</Row>

      <div className="mt-3 space-y-2">
        <Note>
          Depth is the median of valid pixels in the dashed centre region of the box. Background showing through, or an
          occluding object, can pull the estimate; a large spread flags that. XYZ is in the <b>camera frame</b> (x right,
          y down, z forward). No camera-to-excavator calibration is loaded, so excavator coordinates and reachability are not available.
        </Note>
        <Note>{target.association}.</Note>
      </div>
    </Card>
  );
}
