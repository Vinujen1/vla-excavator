import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { DetectionSettings, DetectionState, MarkerSettings } from "../types";
import { Badge, Note, Toggle } from "../ui";

function Slider({ label, value, min, max, step = 1, onChange, suffix = "" }: {
  label: string; value: number; min: number; max: number; step?: number; onChange: (v: number) => void; suffix?: string;
}) {
  return (
    <label className="block">
      <div className="flex justify-between text-xs text-muted"><span>{label}</span><span className="font-mono text-graphite">{value}{suffix}</span></div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-[#2F5EA8]" />
    </label>
  );
}

export function DetectionControls({ detection }: { detection: DetectionState | undefined }) {
  const [local, setLocal] = useState<DetectionSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showClasses, setShowClasses] = useState(false);
  const timer = useRef<number>();

  useEffect(() => {
    if (!local && detection) setLocal(detection.settings);
  }, [detection, local]);

  if (!detection || !local) return <p className="text-muted">Waiting for the console…</p>;

  // Debounced push so dragging a slider doesn't flood the backend.
  const push = (next: DetectionSettings) => {
    setLocal(next);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      api("/api/detection/settings", { method: "PUT", json: next }).then(() => setError(null)).catch((e: Error) => setError(e.message));
    }, 200);
  };
  const setMarker = (patch: Partial<MarkerSettings>) => push({ ...local, marker: { ...local.marker, ...patch } });

  const m = detection.model;
  const statusTone = detection.model_status === "ready" ? "ok" : detection.model_status === "error" ? "fault" : "warn";

  return (
    <div className="max-w-2xl space-y-3">
      <div className="flex items-center gap-2"><span className="text-[13px] text-muted">Detector</span><Badge tone={statusTone}>{detection.model_status}</Badge></div>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Toggle checked={local.yolo_enabled} onChange={(v) => push({ ...local, yolo_enabled: v })} label="YOLO objects" />
          <span className="text-[13px] text-muted">{m.file} · {m.device}{m.half ? " FP16" : ""} · {m.imgsz}px</span>
        </div>
        <Slider label="Confidence threshold" value={local.confidence} min={0.05} max={0.95} step={0.05}
          onChange={(v) => push({ ...local, confidence: Number(v.toFixed(2)) })} />
        {detection.model_error && <Note tone="fault">{detection.model_error}</Note>}

        <div>
          <button onClick={() => setShowClasses(!showClasses)} className="text-[14px] font-semibold text-accent hover:underline">
            {showClasses ? "Hide" : "Show"} the model's {m.classes?.length ?? 0} supported classes
          </button>
          {showClasses && (
            <div className="mt-2 rounded-lg border border-rule bg-paper p-2">
              <p className="mb-1 text-[12px] text-muted">{m.dataset_note}</p>
              <p className="font-mono text-[12px] leading-relaxed text-graphite">{m.classes?.join(", ")}</p>
            </div>
          )}
        </div>

        <div className="border-t border-rule pt-3">
          <Toggle checked={local.marker.enabled} onChange={(v) => setMarker({ enabled: v })} label="Colour-marker mode (OpenCV HSV)" />
          <p className="mt-1 text-[12px] text-faint">
            Marks regions matching a colour as <i>marker candidates</i>. Colour alone doesn't show that something is a cone or marker.
          </p>
          {local.marker.enabled && (
            <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2">
              <label className="col-span-2 text-xs text-muted">Name
                <input value={local.marker.name} onChange={(e) => setMarker({ name: e.target.value.slice(0, 30) })}
                  className="ml-2 rounded border border-rule bg-sheet px-2 py-0.5 text-sm text-graphite" />
              </label>
              <Slider label="Hue min" value={local.marker.h_min} min={0} max={179} onChange={(v) => setMarker({ h_min: v })} />
              <Slider label="Hue max" value={local.marker.h_max} min={0} max={179} onChange={(v) => setMarker({ h_max: v })} />
              <Slider label="Sat min" value={local.marker.s_min} min={0} max={255} onChange={(v) => setMarker({ s_min: v })} />
              <Slider label="Sat max" value={local.marker.s_max} min={0} max={255} onChange={(v) => setMarker({ s_max: v })} />
              <Slider label="Val min" value={local.marker.v_min} min={0} max={255} onChange={(v) => setMarker({ v_min: v })} />
              <Slider label="Val max" value={local.marker.v_max} min={0} max={255} onChange={(v) => setMarker({ v_max: v })} />
              <div className="col-span-2">
                <Slider label="Min area" value={local.marker.min_area_px} min={10} max={5000} step={10} suffix=" px"
                  onChange={(v) => setMarker({ min_area_px: v })} />
              </div>
              <p className="col-span-2 text-[12px] text-faint">
                OpenCV HSV: hue 0–179. Hue min &gt; max wraps around 0 (red is about 170→10). Marker pass: {detection.marker_ms ?? "—"} ms.
              </p>
            </div>
          )}
        </div>
        {error && <Note tone="fault">Settings not applied: {error}</Note>}
      </div>
    </div>
  );
}
