import { useCallback, useEffect, useState } from "react";
import { api, useTelemetry, type LinkState } from "./api";
import { CameraPanel } from "./components/CameraPanel";
import { CommandPanel, type InstructionDraft } from "./components/CommandPanel";
import { CosmosPanel, cosmosStatus } from "./components/CosmosPanel";
import { DetectionControls } from "./components/DetectionControls";
import { EventLog } from "./components/EventLog";
import { HardwarePanel } from "./components/HardwarePanel";
import { MachinePanel } from "./components/MachinePanel";
import { SceneObjects } from "./components/SceneObjects";
import { StopButton, StopStatus, useSoftwareStop } from "./components/StopBar";
import { TargetPanel } from "./components/TargetPanel";
import { TaskPanel } from "./components/TaskPanel";
import type { Telemetry } from "./types";
import { Light, Note, Tray, type Tone } from "./ui";

const MODE = {
  hardware: { label: "Hardware", note: "Real motor outputs", cls: "border-machine bg-machine-wash text-machine-ink" },
  simulation: { label: "Simulation", note: "Simulated Pi, nothing physical moves", cls: "border-accent/40 bg-accent-wash text-accent" },
  perception_preview: { label: "Perception only", note: "No motion controller connected", cls: "border-rule bg-paper text-muted" },
} as const;

function TopBar({ data, link, onStop, sending }: { data: Telemetry | null; link: LinkState; onStop: () => void; sending: boolean }) {
  const cam = data?.camera;
  const camLive = cam?.status === "connected" && (cam.frame_age_s ?? 99) < 1;
  const cosmos = cosmosStatus(data?.decision_providers.find((p) => p.id === "cosmos3"));
  const pi = data?.hardware.pi;
  const piTone: Tone = pi?.fresh ? "ok" : pi?.status === "not configured" ? "muted" : "fault";
  const mode = data ? MODE[data.mode.id] : null;
  const det = data?.detection.model_status;

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
      <div className="flex items-center gap-3">
        <svg width="30" height="30" viewBox="0 0 30 30" aria-hidden>
          <rect x="3" y="20" width="18" height="6" rx="3" fill="none" stroke="#1C2430" strokeWidth="2" />
          <rect x="7" y="13" width="10" height="7" fill="#F2B300" stroke="#1C2430" strokeWidth="2" />
          <path d="M15 14 L22 6 L27 12" fill="none" stroke="#1C2430" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <div className="leading-tight">
          <h1 className="font-display text-[21px] font-bold text-graphite">Excavator console</h1>
          <p className="text-[12px] text-muted">Sandbox rig, Jetson Orin Nano</p>
        </div>
      </div>
      {mode && (
        <span className={`rounded-md border px-3 py-1 ${mode.cls}`} title={data!.mode.label}>
          <span className="font-display text-[15px] font-bold">{mode.label}</span>
          <span className="ml-2 hidden text-[12px] sm:inline">{mode.note}</span>
        </span>
      )}
      <div className="order-last flex w-full flex-wrap items-center gap-x-5 gap-y-1 lg:order-none lg:w-auto lg:flex-1">
        <Light tone={link === "live" ? "ok" : link === "connecting" ? "warn" : "fault"} label={link === "live" ? "Console" : link === "connecting" ? "Connecting" : "Console lost"} />
        <Light tone={camLive ? "ok" : "fault"} label={camLive ? "Camera" : `Camera ${cam?.status ?? "—"}`} detail={camLive ? `${cam!.measured_fps.toFixed(0)} fps` : undefined} />
        <Light tone={det === "ready" ? "ok" : det === "error" ? "fault" : "warn"} label={det === "ready" ? "Detector" : `Detector ${det ?? "—"}`} />
        <Light tone={piTone} label={pi?.fresh ? `Pi ${data?.hardware.state?.backend === "simulation" ? "(simulated)" : ""}` : `Pi ${pi?.status ?? "—"}`} />
        <Light tone={cosmos.tone} label={cosmos.tone === "ok" ? "Cosmos" : cosmos.tone === "muted" ? "Cosmos off" : "Cosmos unverified"} />
      </div>
      <StopButton onStop={onStop} sending={sending} />
    </div>
  );
}

export default function App() {
  const { data, link, log } = useTelemetry();
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [draft, setDraft] = useState<InstructionDraft>({ text: "Plan a scoop-and-dump task for two cycles.", source: "typed" });
  const [provider, setProvider] = useState("rules");
  const [actionError, setActionError] = useState<string | null>(null);
  const softStop = useSoftwareStop();

  // Prefer Cosmos once it has proven itself with a real inference; never switch while it is unverified.
  const cosmosVerified = !!data?.decision_providers.find((p) => p.id === "cosmos3" && p.verified);
  useEffect(() => { if (cosmosVerified) setProvider((p) => (p === "rules" ? "cosmos3" : p)); }, [cosmosVerified]);

  const select = useCallback((frameNumber: number, id: string) => {
    api("/api/target/select", { method: "POST", json: { frame_number: frameNumber, detection_id: id } })
      .then(() => setActionError(null)).catch((e: Error) => setActionError(e.message));
  }, []);
  const clearTarget = () => api("/api/target/clear", { method: "POST" }).catch((e: Error) => setActionError(e.message));
  const hardware = data?.mode.id === "hardware";

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-rule bg-sheet/95 backdrop-blur" style={{ paddingTop: "env(safe-area-inset-top, 0px)" }}>
        {hardware && <div className="hazard h-1.5" aria-hidden />}
        <div className="mx-auto max-w-[1720px] px-4 py-3 lg:px-6">
          <TopBar data={data} link={link} onStop={() => softStop.stop()} sending={softStop.sending} />
        </div>
        <div className="border-t border-rule bg-paper/70">
          <div className="mx-auto max-w-[1720px] px-4 py-1.5 lg:px-6">
            <StopStatus hw={data?.hardware} local={softStop.local} error={softStop.error} />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1720px] space-y-4 px-4 py-4 lg:px-6" style={{ paddingBottom: "max(1rem, env(safe-area-inset-bottom, 0px))" }}>
        {link === "lost" && <Note tone="fault">The console isn't reaching the Jetson. It keeps retrying. Check that the server is running and port 8000 is forwarded. The physical emergency stop works regardless.</Note>}
        {actionError && <Note tone="warn">{actionError}</Note>}
        {hardware && <Note tone="machine"><b>Hardware mode.</b> The Pi drives real motor outputs. Keep the physical emergency stop within reach and the workspace clear.</Note>}

        <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_440px] 2xl:grid-cols-[minmax(0,1fr)_480px]">
          <div className="min-w-0 space-y-4">
            <CameraPanel camera={data?.camera} detection={data?.detection} target={data?.target ?? null}
              hoverId={hoverId} setHoverId={setHoverId} onSelect={select} />
            <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
              <CommandPanel voice={data?.voice} draft={draft} setDraft={setDraft} provider={provider} lastCommand={data?.command ?? null} />
              <TargetPanel target={data?.target ?? null} onClear={clearTarget} />
            </div>
          </div>
          <aside className="min-w-0 space-y-4 xl:sticky xl:top-[124px]">
            <MachinePanel hw={data?.hardware} supState={data?.supervisor.state} />
            <TaskPanel sup={data?.supervisor} providers={data?.decision_providers ?? []} provider={provider} setProvider={setProvider} />
          </aside>
        </div>

        <Tray initial="objects" tabs={[
          { id: "objects", label: `Objects in view${data?.detection.frame ? ` (${data.detection.frame.detections.length})` : ""}`,
            body: <SceneObjects detection={data?.detection} target={data?.target ?? null} hoverId={hoverId} setHoverId={setHoverId} onSelect={select} /> },
          { id: "detection", label: "Detection settings", body: <DetectionControls detection={data?.detection} /> },
          { id: "pi", label: "Pi and calibration", body: <HardwarePanel hw={data?.hardware} calibration={data?.calibration} /> },
          { id: "cosmos", label: "Cosmos", body: <CosmosPanel providers={data?.decision_providers ?? []} /> },
          { id: "log", label: "Event log", body: <EventLog log={log} /> },
        ]} />
      </main>
    </div>
  );
}
