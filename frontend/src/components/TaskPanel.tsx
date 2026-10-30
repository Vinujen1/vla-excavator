import { useState } from "react";
import { api, fmtNum, fmtTime } from "../api";
import type { DecisionProviderStatus, SupervisorState, SupervisorStateName, TaskSpec } from "../types";
import { Badge, Button, Card, Note, Row, type Tone } from "../ui";

const STATE: Record<SupervisorStateName, { tone: Tone; label: string }> = {
  DISARMED: { tone: "muted", label: "Disarmed" }, READY: { tone: "accent", label: "Armed, ready" },
  PLANNING: { tone: "accent", label: "Planning" }, AWAITING_APPROVAL: { tone: "warn", label: "Needs approval" },
  EXECUTING: { tone: "ok", label: "Executing" }, VERIFYING: { tone: "ok", label: "Verifying" },
  PAUSED: { tone: "warn", label: "Paused" }, COMPLETED: { tone: "ok", label: "Completed" },
  FAULT: { tone: "fault", label: "Fault" }, STOPPED: { tone: "fault", label: "Stopped" },
};

const SKILL: Record<string, string> = {
  rotate_to_dig: "To dig", lower_to_dig: "Lower", scoop: "Scoop", lift: "Lift",
  rotate_to_dump: "To dump", dump: "Dump", return_home: "Return home",
};

function SpecRows({ spec }: { spec: TaskSpec }) {
  return (
    <div>
      <Row label="Goal">Scoop and dump, {spec.cycles} cycle{spec.cycles > 1 ? "s" : ""}</Row>
      <Row label="Target">{spec.target
        ? `${spec.target.label} (${spec.target.detection_id}), depth ${spec.target.depth_status.replace(/_/g, " ")}${spec.target.depth_m ? ` ${spec.target.depth_m.toFixed(2)} m` : ""}`
        : "None selected"}</Row>
      <Row label="Runs in">{spec.mode === "simulation" ? <Badge tone="accent">Simulation</Badge> : <Badge tone="machine">Hardware</Badge>}</Row>
      <Row label="Time limit">{spec.timeout_s.toFixed(0)} s of active time</Row>
      <Row label="Instruction"><span className="italic">“{spec.instruction.text}”</span> <span className="text-faint">{spec.instruction.source.replace("_", " ")}</span></Row>
    </div>
  );
}

/** One row per cycle, so progress reads as "cycle 2, scooping" at a glance. */
function Timeline({ plan, done, running, hardware }: { plan: string[]; done: number; running: boolean; hardware: boolean }) {
  const perCycle = 6;
  const cycles = Math.floor((plan.length - 1) / perCycle);
  const active = hardware ? "border-machine bg-machine text-graphite" : "border-accent bg-accent text-white";
  const seg = (i: number, s: string) => {
    const state = i < done ? "done" : i === done && running ? "current" : "todo";
    const cls = state === "done" ? "border-ok/30 bg-ok-wash text-ok" : state === "current" ? `${active} animate-beacon` : "border-rule bg-sheet text-faint";
    return <li key={i} className={`flex-1 truncate rounded border px-1.5 py-1 text-center text-[12px] font-medium ${cls}`} title={SKILL[s]}>{SKILL[s]}</li>;
  };
  return (
    <div className="space-y-1.5">
      {Array.from({ length: cycles }, (_, c) => (
        <div key={c} className="flex items-center gap-2">
          <span className="w-14 shrink-0 font-display text-[14px] font-semibold text-muted">Cycle {c + 1}</span>
          <ol className="flex min-w-0 flex-1 gap-1">{plan.slice(c * perCycle, (c + 1) * perCycle).map((s, k) => seg(c * perCycle + k, s))}</ol>
        </div>
      ))}
      <div className="flex items-center gap-2">
        <span className="w-14 shrink-0 font-display text-[14px] font-semibold text-muted">Finish</span>
        <ol className="flex flex-1">{seg(plan.length - 1, plan[plan.length - 1])}</ol>
      </div>
    </div>
  );
}

export function TaskPanel({ sup, providers, provider, setProvider }: {
  sup: SupervisorState | undefined; providers: DecisionProviderStatus[]; provider: string; setProvider: (p: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!sup) return <Card title="Task"><p className="text-muted">Waiting for the console…</p></Card>;

  const call = async (path: string) => {
    setBusy(true); setError(null);
    try { await api(path, { method: "POST" }); } catch (e) { setError((e as Error).message); }
    setBusy(false);
  };
  const s = sup.state;
  const { task, progress: p, pending } = sup;
  const running = s === "EXECUTING" || s === "VERIFYING";
  const blocked = sup.blockers.length > 0;
  const hardware = (task?.mode ?? pending?.spec?.mode) === "hardware";
  const timeFrac = task ? Math.min(1, p.active_s / task.timeout_s) : 0;

  return (
    <Card title="Task" right={<Badge tone={STATE[s].tone} pulse={running || s === "PLANNING" || s === "AWAITING_APPROVAL"}>{STATE[s].label}</Badge>}>
      <label className="mb-3 flex min-w-0 items-center gap-2 text-[13px] text-muted">
        Decisions by
        <select value={provider} onChange={(e) => setProvider(e.target.value)} disabled={running || s === "PLANNING"}
          className="w-0 min-w-0 flex-1 rounded-md border border-rule bg-sheet px-2 py-1.5 text-[14px] text-graphite">
          {providers.map((x) => <option key={x.id} value={x.id} disabled={!x.available}>{x.available ? x.name : `${x.name}: not connected`}</option>)}
        </select>
      </label>

      {sup.reason && !running && <p className="mb-3 text-[14px] text-graphite">{sup.reason}</p>}
      {sup.last_plan_error && s !== "PLANNING" && <div className="mb-3"><Note tone="fault">{sup.last_plan_error}</Note></div>}

      {s === "DISARMED" && !pending && (
        <p className="text-[14px] leading-relaxed text-muted">
          Send a task from the command bar, for example <span className="text-graphite">“Start autonomous scoop-and-dump for two cycles”</span>.
          You approve the goal and target before anything is armed.
        </p>
      )}
      {s === "PLANNING" && <Note tone="accent">Waiting for {providers.find((x) => x.id === provider)?.name ?? provider}. Stop and cancel still respond while it works.</Note>}

      {s === "AWAITING_APPROVAL" && pending && (
        <div className="animate-rise rounded-md border border-warn/40 bg-warn-wash/60 p-3">
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="font-display text-[16px] font-semibold text-graphite">Check the goal and target</h3>
            <span className="text-[12px] text-muted">{pending.provider_kind === "reasoning_model" ? "Cosmos" : "Rule-based"}, {pending.latency_ms} ms</span>
          </div>
          {pending.spec && <SpecRows spec={pending.spec} />}
          <p className="mt-2 text-[13px] text-muted">Why: <span className="text-graphite">{String(pending.proposal.explanation ?? "")}</span></p>
          {pending.errors.length > 0 && <div className="mt-2"><Note tone="fault"><ul className="list-disc pl-4">{pending.errors.map((e) => <li key={e}>{e}</li>)}</ul></Note></div>}
          {pending.warnings.length > 0 && <div className="mt-2"><Note tone="warn"><ul className="list-disc pl-4">{pending.warnings.map((e) => <li key={e}>{e}</li>)}</ul></Note></div>}
          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="primary" size="lg" disabled={!pending.spec || blocked || busy} onClick={() => call("/api/task/approve")}
              title={blocked ? "Clear the items under “Can't run yet” first" : undefined}>
              {pending.spec?.start_requested ? "Approve and start" : "Approve and arm"}
            </Button>
            <Button size="lg" onClick={() => call("/api/task/reject")} disabled={busy}>Reject</Button>
          </div>
        </div>
      )}

      {task && s !== "AWAITING_APPROVAL" && (
        <div className="space-y-3">
          <Timeline plan={task.plan} done={p.completed_skills} running={running} hardware={hardware} />
          <div>
            <div className="mb-1 flex justify-between text-[12px] text-muted">
              <span>{p.current_move && running ? `Moving ${p.current_move.joint} to ${p.current_move.pose} (${p.current_move.target}°), move ${p.current_move.index} of ${p.current_move.of}` : `${p.completed_skills} of ${p.total_skills} steps done`}</span>
              <span className="tabular-nums">{fmtNum(p.active_s, 0)} / {task.timeout_s.toFixed(0)} s</span>
            </div>
            <div className="h-1 overflow-hidden rounded bg-paper" title="Time used of the task's time limit">
              <div className={`h-full ${timeFrac > 0.8 ? "bg-warn" : "bg-faint"}`} style={{ width: `${timeFrac * 100}%` }} />
            </div>
          </div>
          {(p.pausing || p.cancelling) && <Note tone="warn">{p.pausing ? "Pausing" : "Cancelling"} after the current move finishes. Use Stop to halt now.</Note>}
          <div className="flex flex-wrap gap-2">
            {s === "READY" && <Button variant="primary" size="lg" onClick={() => call("/api/task/start")} disabled={busy || blocked}>Start</Button>}
            {s === "READY" && <Button size="lg" onClick={() => call("/api/task/cancel")} disabled={busy}>Disarm</Button>}
            {running && <Button size="lg" onClick={() => call("/api/task/pause")} disabled={busy || p.pausing}>Pause</Button>}
            {s === "PAUSED" && <Button variant="primary" size="lg" onClick={() => call("/api/task/resume")} disabled={busy || blocked}>Resume</Button>}
            {(running || s === "PAUSED") && <Button size="lg" onClick={() => call("/api/task/cancel")} disabled={busy || p.cancelling}>Cancel task</Button>}
            {(s === "COMPLETED" || s === "FAULT" || s === "STOPPED") && <Button size="lg" onClick={() => call("/api/task/reset")} disabled={busy}>Reset</Button>}
          </div>
        </div>
      )}
      {(s === "FAULT" || s === "STOPPED") && !task && <Button size="lg" onClick={() => call("/api/task/reset")} disabled={busy}>Reset</Button>}

      {blocked && !running && (
        <div className="mt-3">
          <Note tone={["AWAITING_APPROVAL", "READY", "PAUSED"].includes(s) ? "warn" : "muted"}>
            <span className="font-semibold">Can't run yet</span>
            <ul className="mt-1 list-disc space-y-0.5 pl-4">{sup.blockers.map((b) => <li key={b}>{b}</li>)}</ul>
          </Note>
        </div>
      )}
      {error && <div className="mt-2"><Note tone="fault">{error}</Note></div>}

      {sup.decisions.length > 0 && (
        <details className="group mt-4 border-t border-rule pt-3" open={running}>
          <summary className="cursor-pointer list-none text-[14px] font-semibold text-graphite">
            Decisions <span className="font-normal text-muted">({sup.decisions.length} recent)</span>
          </summary>
          <ol className="mt-2 max-h-60 space-y-1.5 overflow-auto pr-1">
            {sup.decisions.slice().reverse().map((d, i) => {
              const choice = d.output ? String(d.output.next_skill ?? d.output.intent ?? "") : "no answer";
              const used = !d.discarded && !d.error && (d.verdict === undefined || d.verdict === "accept" || d.verdict === "complete" || d.verdict === "retry");
              return (
                <li key={i} className="border-l-2 pl-2.5 text-[13px]" style={{ borderColor: d.discarded ? "#B45309" : used ? "#23824F" : "#D62F22" }}>
                  <div className="flex flex-wrap items-baseline gap-x-2 text-muted">
                    <span className="tabular-nums">{fmtTime(d.at)}</span>
                    <span className="font-semibold text-graphite">{SKILL[choice] ?? choice.replace(/_/g, " ")}</span>
                    <span>{d.provider_kind === "reasoning_model" ? "Cosmos" : "rules"}, {d.latency_ms} ms</span>
                    {d.discarded && <span className="text-warn">ignored: arrived late</span>}
                    {!d.discarded && d.verdict && d.verdict.startsWith("pause") && <span className="text-warn">paused the task</span>}
                  </div>
                  {d.output?.explanation ? <div className="text-graphite">{String(d.output.explanation)}</div> : null}
                  {d.verdict?.startsWith("pause:") && <div className="text-warn">{d.verdict.slice(7)}</div>}
                  {d.error && <div className="text-fault">{d.error}</div>}
                </li>
              );
            })}
          </ol>
        </details>
      )}
    </Card>
  );
}
