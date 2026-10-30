import { useState } from "react";
import { api, fmtNum } from "../api";
import type { HardwareState, SupervisorStateName } from "../types";
import { Badge, Button, Card, Note } from "../ui";
import { ArmDiagram } from "./ArmDiagram";

const SAFE_TO_REARM: SupervisorStateName[] = ["DISARMED", "AWAITING_APPROVAL", "STOPPED", "FAULT", "COMPLETED", "PAUSED"];

export function MachinePanel({ hw, supState }: { hw: HardwareState | undefined; supState: SupervisorStateName | undefined }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pi = hw?.state ?? null;
  const moving = (pi?.current_action?.params?.joint as string | undefined) ?? null;

  const rearm = async () => {
    setBusy(true); setError(null);
    try { await api("/api/pi/rearm", { method: "POST" }); } catch (e) { setError((e as Error).message); }
    setBusy(false);
  };

  let status;
  if (!hw || hw.pi.status === "not configured") status = <Badge tone="muted">No Pi configured</Badge>;
  else if (hw.pi.status !== "connected") status = <Badge tone="fault" pulse>Pi {hw.pi.status}</Badge>;
  else if (!hw.pi.fresh) status = <Badge tone="warn">Pi data stale</Badge>;
  else if (pi?.fault) status = <Badge tone="fault">Fault</Badge>;
  else if (pi?.latched) status = <Badge tone="warn">Latched</Badge>;
  else if (pi?.state === "moving") status = <Badge tone={pi.backend === "hardware" ? "machine" : "accent"} pulse>Moving {moving}</Badge>;
  else status = <Badge tone="ok">Armed, idle</Badge>;

  return (
    <Card title="Machine" right={status}>
      <ArmDiagram pi={pi} movingJoint={moving} />
      {pi && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-[13px] text-muted">
          <span>Outputs <b className={pi.energised ? (pi.backend === "hardware" ? "text-machine-ink" : "text-accent") : "text-graphite"}>{pi.energised ? (pi.backend === "hardware" ? "energised" : "on (simulated)") : "all off"}</b></span>
          <span>Telemetry <b className="text-graphite tabular-nums">{fmtNum((hw?.pi.state_age_s ?? 0) * 1000, 0)} ms</b> old</span>
          <span>Heartbeat <b className="text-graphite tabular-nums">{hw?.pi.heartbeat_rtt_ms ?? "—"} ms</b></span>
        </div>
      )}
      {pi?.fault && <div className="mt-3"><Note tone="fault">{pi.fault.code.replace(/_/g, " ")}: {pi.fault.message}</Note></div>}
      {pi?.latched && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-md border border-warn/30 bg-warn-wash px-3 py-2">
          <span className="text-[13px] text-[#7A3A06]">
            {pi.latched.startsWith("startup") ? "The Pi starts latched." : pi.latched.startsWith("stopped") ? "Latched by a stop." : "Latched by a fault."} Nothing moves until you rearm it.
          </span>
          <Button onClick={rearm} disabled={busy || !supState || !SAFE_TO_REARM.includes(supState)}>Rearm Pi</Button>
        </div>
      )}
      {hw && hw.pi.status !== "connected" && hw.pi.error && <p className="mt-2 text-[13px] text-fault">{hw.pi.error}</p>}
      {error && <div className="mt-2"><Note tone="fault">{error}</Note></div>}
    </Card>
  );
}
