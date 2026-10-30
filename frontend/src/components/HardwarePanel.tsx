import { fmtNum, fmtTime } from "../api";
import type { HardwareState, Telemetry } from "../types";
import { Badge, Row } from "../ui";

/** Pi link, joint table and calibration status: the detail behind the Machine panel. */
export function HardwarePanel({ hw, calibration }: { hw: HardwareState | undefined; calibration: Telemetry["calibration"] | undefined }) {
  if (!hw) return <p className="text-muted">Waiting for the console…</p>;
  const pi = hw.state;
  const linkTone = hw.pi.status === "connected" ? (hw.pi.fresh ? "ok" : "warn") : hw.pi.status === "not configured" ? "muted" : "fault";

  return (
    <div className="grid gap-x-8 gap-y-4 lg:grid-cols-2">
      <div>
        <h3 className="mb-1 font-display text-[16px] font-semibold">Raspberry Pi link</h3>
        <Row label="Status"><Badge tone={linkTone}>{hw.pi.status === "not configured" ? "Not configured" : hw.pi.status}</Badge></Row>
        <Row label="Output backend">{pi ? (pi.backend === "simulation" ? <Badge tone="accent">Simulation</Badge> : <Badge tone="machine">Hardware GPIO</Badge>) : "—"}</Row>
        <Row label="Telemetry age">{hw.pi.state_age_s != null ? `${fmtNum(hw.pi.state_age_s, 2)} s` : "—"}</Row>
        <Row label="Last contact">{hw.pi.last_contact ? fmtTime(hw.pi.last_contact) : "—"}</Row>
        <Row label="Heartbeat">{pi ? `seen ${fmtNum(pi.heartbeat.age_s, 2)} s ago, watchdog ${pi.heartbeat.watchdog_s} s` : "—"}</Row>
        <Row label="Clock offset">{pi?.heartbeat.clock_offset_ms != null ? `${pi.heartbeat.clock_offset_ms} ms` : "—"}</Row>
        <Row label="Pin mapping">{pi ? (pi.pins_verified ? "Verified" : <span className="text-warn">Not verified (proposed wiring)</span>) : "—"}</Row>
        <Row label="Commissioning jog">{pi ? (pi.commissioning ? <span className="text-warn">Enabled</span> : "Off") : "—"}</Row>
        {hw.pi.error && <p className="mt-1 text-[13px] text-fault">{hw.pi.error}</p>}
      </div>
      <div>
        <h3 className="mb-1 font-display text-[16px] font-semibold">Joints</h3>
        {pi ? (
          <table className="w-full text-left text-[14px]">
            <thead className="text-[12px] text-muted">
              <tr><th className="py-1 font-medium">Joint</th><th className="font-medium">Position</th><th className="font-medium">Feedback</th><th className="font-medium">Limits</th></tr>
            </thead>
            <tbody className="tabular-nums">
              {Object.entries(pi.joints).map(([name, j]) => (
                <tr key={name} className="border-t border-rule/70">
                  <td className="py-1.5 capitalize">{name}</td>
                  <td>{j.position != null ? `${j.position.toFixed(1)} ${j.units === "deg" ? "°" : j.units ?? ""}` : "—"}</td>
                  <td className={j.feedback === "simulated" ? "text-accent" : j.feedback ? "" : "text-warn"}>
                    {j.feedback ? `${j.feedback}${j.feedback_age_s != null ? `, ${(j.feedback_age_s * 1000).toFixed(0)} ms` : ""}` : "none"}
                  </td>
                  <td className="text-muted">{j.limits ? `${j.limits[0]} to ${j.limits[1]}` : "not calibrated"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="text-[14px] text-muted">No joint data without a Pi connection.</p>}
        <h3 className="mb-1 mt-4 font-display text-[16px] font-semibold">Calibration</h3>
        <Row label="Hardware">{calibration?.hardware?.loaded ? (calibration.hardware.verified ? "Loaded and verified" : <span className="text-warn">Loaded, not verified</span>) : <span className="text-warn">Missing</span>}</Row>
        <Row label="Simulation">{calibration?.simulation?.loaded ? "Loaded" : <span className="text-warn">{calibration?.simulation?.error ?? "Missing"}</span>}</Row>
        {pi?.pwm_notes && (
          <details className="mt-2 text-[13px] text-muted">
            <summary className="cursor-pointer">PWM pins</summary>
            <ul className="mt-1 list-disc pl-4">{pi.pwm_notes.map((n) => <li key={n}>{n}</li>)}</ul>
          </details>
        )}
      </div>
    </div>
  );
}
