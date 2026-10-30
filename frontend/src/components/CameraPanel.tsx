import { useEffect, useState } from "react";
import { fmtNum } from "../api";
import type { CameraState, Detection, DetectionState, Target } from "../types";
import { Badge, Card, Stat, Toggle } from "../ui";

interface Props {
  camera: CameraState | undefined;
  detection: DetectionState | undefined;
  target: Target | null;
  hoverId: string | null;
  setHoverId: (id: string | null) => void;
  onSelect: (frameNumber: number, id: string) => void;
}

const colourFor = (d: Detection) => (d.source === "color_marker" ? "#f472b6" : "#22d3ee");

export function CameraPanel({ camera, detection, target, hoverId, setHoverId, onSelect }: Props) {
  const [view, setView] = useState<"color" | "depth">("color");
  const [overlay, setOverlay] = useState(true);
  const [labels, setLabels] = useState(true);
  const [streamKey, setStreamKey] = useState(0);

  const w = camera?.configured.width ?? 640;
  const h = camera?.configured.height ?? 480;
  const live = camera?.status === "connected" && (camera.frame_age_s ?? 99) < 1;
  const frame = detection?.frame ?? null;
  const boxesAge = detection?.frame_age_s ?? null;
  const boxesFresh = boxesAge !== null && boxesAge < 1;

  // When the camera comes back, reopen the stream in case the browser gave up on it.
  useEffect(() => { if (live) setStreamKey((k) => k + 1); }, [live]);

  const sel = target?.detection;
  const selectedId = target && frame && target.last_seen_frame_number === frame.frame_number ? sel?.id : null;

  return (
    <Card
      title="Live camera"
      right={
        <div className="flex flex-wrap items-center gap-3">
          <div className="inline-flex overflow-hidden rounded-lg border border-rule text-xs">
            {(["color", "depth"] as const).map((v) => (
              <button key={v} onClick={() => setView(v)}
                className={`px-3 py-1 transition-colors ${view === v ? "bg-sheet text-graphite shadow-sm" : "text-muted hover:text-graphite"}`}>
                {v === "color" ? "Colour" : "Depth"}
              </button>
            ))}
          </div>
          <Toggle checked={overlay} onChange={setOverlay} label="Boxes" />
          <Toggle checked={labels} onChange={setLabels} label="Labels" />
        </div>
      }>
      <div className="relative mx-auto w-full overflow-hidden rounded-lg bg-black" style={{ aspectRatio: `${w} / ${h}` }}>
        <img key={`${view}-${streamKey}`} src={`/video.mjpg?view=${view}&k=${streamKey}`} alt="Live camera stream"
          className="absolute inset-0 h-full w-full object-contain"
          onError={() => window.setTimeout(() => setStreamKey((k) => k + 1), 2000)} />

        {overlay && (
          <svg viewBox={`0 0 ${w} ${h}`} className="absolute inset-0 h-full w-full" preserveAspectRatio="xMidYMid meet">
            {frame && boxesFresh && frame.detections.map((d) => {
              const isSel = d.id === selectedId;
              const isHover = d.id === hoverId;
              const c = colourFor(d);
              const text = `${d.label}${d.confidence !== null ? ` ${(d.confidence * 100).toFixed(0)}%` : ""}${d.depth.depth_m !== null ? ` · ${d.depth.depth_m.toFixed(2)} m` : ""}`;
              return (
                <g key={d.id} className="cursor-pointer" onClick={() => onSelect(frame.frame_number, d.id)}
                  onMouseEnter={() => setHoverId(d.id)} onMouseLeave={() => setHoverId(null)}>
                  <rect x={d.bbox.x1} y={d.bbox.y1} width={d.bbox.x2 - d.bbox.x1} height={d.bbox.y2 - d.bbox.y1}
                    fill={isHover || isSel ? `${c}22` : "transparent"} stroke={isSel ? "#ffffff" : c}
                    strokeWidth={isSel ? 3 : isHover ? 2.5 : 1.5} style={{ pointerEvents: "all", transition: "all 120ms" }} />
                  {labels && (
                    <g style={{ pointerEvents: "none" }}>
                      <rect x={d.bbox.x1} y={Math.max(0, d.bbox.y1 - 18)} width={text.length * 6.6 + 8} height={17} fill="#070c17cc" />
                      <text x={d.bbox.x1 + 4} y={Math.max(12, d.bbox.y1 - 5)} fill={isSel ? "#fff" : c} fontSize={12}
                        fontFamily="ui-monospace, monospace">{text}</text>
                    </g>
                  )}
                </g>
              );
            })}
            {/* Selected target: drawn from its last matched detection. Amber dashed when stale. */}
            {sel && (
              <g style={{ pointerEvents: "none" }}>
                <rect x={sel.bbox.x1 - 3} y={sel.bbox.y1 - 3} width={sel.bbox.x2 - sel.bbox.x1 + 6} height={sel.bbox.y2 - sel.bbox.y1 + 6}
                  fill="none" stroke={target!.stale ? "#f59e0b" : "#ffffff"} strokeWidth={2}
                  strokeDasharray={target!.stale ? "8 6" : undefined} />
                {sel.depth.roi && (
                  <rect x={sel.depth.roi.x1} y={sel.depth.roi.y1} width={sel.depth.roi.x2 - sel.depth.roi.x1}
                    height={sel.depth.roi.y2 - sel.depth.roi.y1} fill="none" stroke="#ffffff99" strokeWidth={1} strokeDasharray="3 3" />
                )}
                <text x={sel.bbox.x1} y={Math.min(h - 4, sel.bbox.y2 + 16)} fill={target!.stale ? "#f59e0b" : "#fff"} fontSize={12}
                  fontWeight={600} fontFamily="ui-monospace, monospace">
                  {target!.stale ? "TARGET (STALE)" : "TARGET"}
                </text>
              </g>
            )}
          </svg>
        )}

        {!live && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-sheet/90 text-center backdrop-blur-sm">
            <Badge tone="fault" pulse>Camera {camera?.status ?? "unknown"}</Badge>
            <p className="max-w-md px-6 text-sm text-graphite">{camera?.error ?? "Waiting for frames…"}</p>
            <p className="text-xs text-faint">The backend reconnects automatically every 2 s.</p>
          </div>
        )}
        {live && overlay && detection && !boxesFresh && detection.model_status === "ready" && (
          <div className="absolute left-3 top-3"><Badge tone="warn">Detections stale</Badge></div>
        )}
      </div>

      <div className="mt-3 grid grid-cols-3 gap-x-4 gap-y-2 sm:grid-cols-6">
        <Stat label="Camera" value={fmtNum(camera?.measured_fps, 1)} unit="fps" tone={live ? undefined : "fault"} />
        <Stat label="Frame age" value={camera?.frame_age_s !== undefined ? Math.round(camera.frame_age_s * 1000) : "—"} unit="ms"
          tone={(camera?.frame_age_s ?? 99) > 0.5 ? "warn" : undefined} />
        <Stat label="Detection" value={fmtNum(detection?.detection_fps, 1)} unit="fps" />
        <Stat label="Inference" value={fmtNum(detection?.inference_ms, 1)} unit="ms" />
        <Stat label="Boxes age" value={boxesAge !== null ? Math.round(boxesAge * 1000) : "—"} unit="ms"
          tone={boxesAge !== null && boxesAge > 0.5 ? "warn" : undefined} />
        <Stat label="Frame #" value={camera?.seq ?? "—"} />
      </div>
      <p className="mt-2 text-[12px] text-faint">
        {view === "depth" ? "Depth view: red = near, blue = far, black = no depth (display only). " : ""}
        Boxes come from the latest detection frame and may trail the video by about one inference cycle.
        {camera?.device?.usb && camera.device.usb.startsWith("2") && <span className="text-warn"> Camera link is USB {camera.device.usb}.</span>}
      </p>
    </Card>
  );
}
