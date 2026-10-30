import { useState } from "react";
import { api, fmtTime } from "../api";
import type { DecisionProviderStatus } from "../types";
import { Badge, Button, Note, Row } from "../ui";

interface ProbeReport {
  endpoint: string | null; model: string; models_listed: string[] | null; model_available: boolean | null;
  inference_ok: boolean; latency_ms: number | null; error: string | null; sample_intent?: string;
}

export function cosmosStatus(c: DecisionProviderStatus | undefined) {
  if (!c?.available) return { tone: "muted" as const, label: "Not connected" };
  if (c.verified) return { tone: "ok" as const, label: "Verified by a real request" };
  if (c.last_error) return { tone: "fault" as const, label: "Error" };
  return { tone: "warn" as const, label: "Configured, not yet verified" };
}

export function CosmosPanel({ providers }: { providers: DecisionProviderStatus[] }) {
  const c = providers.find((p) => p.id === "cosmos3");
  const [probing, setProbing] = useState(false);
  const [report, setReport] = useState<ProbeReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!c) return null;

  const probe = async () => {
    setProbing(true); setError(null);
    try { setReport(await api<ProbeReport>("/api/cosmos/probe", { method: "POST" })); } catch (e) { setError((e as Error).message); }
    setProbing(false);
  };
  const st = cosmosStatus(c);

  return (
    <div className="grid gap-x-8 gap-y-4 lg:grid-cols-2">
      <div>
        <div className="mb-1 flex items-center justify-between gap-2">
          <h3 className="font-display text-[16px] font-semibold">Cosmos 3 reasoner</h3>
          <Badge tone={st.tone} pulse={c.busy}>{c.busy ? "Thinking…" : st.label}</Badge>
        </div>
        <Row label="Endpoint">{c.endpoint ?? <span className="text-faint">Not set</span>}</Row>
        <Row label="Model requested">{c.model}</Row>
        <Row label="Model the server reported">{c.reported_model ?? <span className="text-faint">—</span>}</Row>
        <Row label="Last answer took">{c.last_latency_ms != null ? `${c.last_latency_ms} ms` : "—"}</Row>
        <Row label="Last success">{c.last_ok_at ? fmtTime(c.last_ok_at) : "—"}</Row>
        <Row label="Requests this session">{c.requests ?? 0}</Row>
        {c.last_error && <p className="mt-1 text-[13px] text-fault">{c.last_error}</p>}
        <div className="mt-3">
          <Button onClick={probe} disabled={probing || !c.available}>{probing ? "Sending test request…" : "Send a test request"}</Button>
        </div>
        {report && (
          <div className="mt-2">
            <Note tone={report.inference_ok ? "ok" : "fault"}>
              {report.inference_ok ? `Answered in ${report.latency_ms} ms (read the test instruction as “${report.sample_intent}”).` : `Test failed: ${report.error}`}
              {report.models_listed && <> Models on the server: {report.models_listed.join(", ") || "none"}{report.model_available === false && ". The requested model isn't among them"}.</>}
            </Note>
          </div>
        )}
        {error && <div className="mt-2"><Note tone="fault">{error}</Note></div>}
      </div>
      <div className="space-y-2 text-[14px] leading-relaxed text-muted">
        <p>
          Cosmos proposes what you meant, which detected object is the target, and the next step, with a short reason.
          It never sends motor commands or distances: depth and positions come from the camera and calibration, and every proposal is checked before anything moves.
        </p>
        {!c.available && (
          <p>
            Cosmos 3 can't run on this Orin Nano with JetPack 6.2. Serve it on a supported GPU machine
            (<span className="font-mono text-[13px] text-graphite">vllm serve nvidia/Cosmos3-Edge</span> or the Cosmos 3 Reasoner NIM),
            then set <span className="font-mono text-[13px] text-graphite">VLA_COSMOS_BASE_URL</span> in <span className="font-mono text-[13px] text-graphite">.env</span> and restart the console.
            Until then, the rule-based provider makes the decisions and is labelled as such.
          </p>
        )}
      </div>
    </div>
  );
}
