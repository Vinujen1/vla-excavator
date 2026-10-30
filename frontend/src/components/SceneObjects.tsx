import type { DetectionState, Target } from "../types";
import { Badge } from "../ui";
import { depthText, depthTone } from "./TargetPanel";

interface Props {
  detection: DetectionState | undefined;
  target: Target | null;
  hoverId: string | null;
  setHoverId: (id: string | null) => void;
  onSelect: (frameNumber: number, id: string) => void;
}

export function SceneObjects({ detection, target, hoverId, setHoverId, onSelect }: Props) {
  const frame = detection?.frame ?? null;
  const dets = frame?.detections ?? [];
  const fresh = (detection?.frame_age_s ?? 99) < 1;

  return (
    <div>
      <p className="mb-2 text-[13px] text-muted">{frame ? `${dets.length} object${dets.length === 1 ? "" : "s"} in detection frame ${frame.frame_number}` : "No detection frame yet"}</p>
      {!fresh && frame && <p className="mb-2 text-xs text-warn">Detections are not current.</p>}
      {dets.length === 0 ? (
        <p className="text-sm text-muted">
          {detection?.model_status === "ready" || detection?.settings.marker.enabled
            ? "No objects above the confidence threshold in the current frame."
            : `Detector ${detection?.model_status ?? "unavailable"}.`}
        </p>
      ) : (
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 bg-sheet text-[12px] font-medium text-muted">
              <tr><th className="py-1.5 pr-2">Object</th><th className="pr-2">Conf.</th><th className="pr-2">Depth</th><th className="pr-2">Centre px</th><th /></tr>
            </thead>
            <tbody>
              {dets.map((d) => {
                const selected = target && !target.stale && target.last_seen_frame_number === frame!.frame_number && target.detection.id === d.id;
                return (
                  <tr key={d.id} onMouseEnter={() => setHoverId(d.id)} onMouseLeave={() => setHoverId(null)}
                    onClick={() => onSelect(frame!.frame_number, d.id)}
                    className={`cursor-pointer border-t border-rule/70 transition-colors ${selected ? "bg-accent-wash" : hoverId === d.id ? "bg-paper" : ""}`}>
                    <td className="py-1.5 pr-2">
                      <span className={`mr-2 inline-block h-2 w-2 rounded-sm ${d.source === "yolo" ? "bg-accent" : "bg-[#C2185B]"}`} />
                      {d.label}
                    </td>
                    <td className="pr-2 tabular-nums">{d.confidence !== null ? `${(d.confidence * 100).toFixed(0)}%` : "—"}</td>
                    <td className="pr-2">
                      <span className={`tabular-nums ${depthTone(d.depth) === "ok" ? "text-graphite" : depthTone(d.depth) === "warn" ? "text-warn" : "text-fault"}`}>
                        {depthText(d.depth)}
                      </span>
                    </td>
                    <td className="pr-2 tabular-nums text-muted">
                      {Math.round((d.bbox.x1 + d.bbox.x2) / 2)}, {Math.round((d.bbox.y1 + d.bbox.y2) / 2)}
                    </td>
                    <td className="text-right">{selected && <Badge tone="accent">target</Badge>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-2 text-[12px] text-faint">IDs are per detection frame. Click a row or a box to select it.</p>
    </div>
  );
}
