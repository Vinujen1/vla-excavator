import { useEffect, useState } from "react";
import { api, fmtTime } from "../api";
import type { HardwareState, StopAck } from "../types";

/** The software stop. It sends a real stop to the Pi and reports whether the Pi acknowledged it.
 *  It supplements the physical emergency stop and never replaces it. Esc also triggers it. */
export function useSoftwareStop() {
  const [sending, setSending] = useState(false);
  const [local, setLocal] = useState<StopAck | null>(null);
  const [error, setError] = useState<string | null>(null);

  const stop = async (source = "dashboard button") => {
    setSending(true); setError(null);
    try { setLocal(await api<StopAck>("/api/stop", { method: "POST", json: { source } })); }
    catch (e) { setError(`The stop request didn't reach the Jetson: ${(e as Error).message}`); }
    setSending(false);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") stop("Esc key"); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return { stop, sending, local, error };
}

export function StopButton({ onStop, sending }: { onStop: () => void; sending: boolean }) {
  return (
    <button onClick={onStop} disabled={sending} aria-keyshortcuts="Escape"
      className="group relative inline-flex items-center gap-3 overflow-hidden rounded-md bg-fault py-2 pl-3 pr-4 font-display text-[17px] font-bold text-white shadow-[0_2px_0_#9E1F15] transition-colors hover:bg-[#B9261B] active:translate-y-px active:shadow-none disabled:opacity-70">
      <span className="hazard h-6 w-2 rounded-sm" aria-hidden />
      {sending ? "Stopping…" : "Stop"}
      <kbd className="rounded border border-white/40 px-1.5 text-[12px] font-medium text-white/85">Esc</kbd>
    </button>
  );
}

export function StopStatus({ hw, local, error }: { hw: HardwareState | undefined; local: StopAck | null; error: string | null }) {
  const ack = hw?.last_stop ?? local;
  const piConnected = hw?.pi.status === "connected";
  let msg;
  if (error) msg = <span className="font-semibold text-fault">{error}</span>;
  else if (ack?.acknowledged) msg = <span className="text-ok">Last stop at {fmtTime(ack.sent_at)} was acknowledged by the Pi in {ack.ack_latency_ms} ms. Outputs {ack.pi_response?.outputs_off ? "off" : "not confirmed off"}, controller latched.</span>;
  else if (ack) msg = <span className="font-semibold text-fault">Last stop at {fmtTime(ack.sent_at)} was not acknowledged: {ack.error}</span>;
  else msg = <span>{piConnected ? "Stop sends a real stop request to the Pi." : "No Pi is connected, so Stop has nothing to reach yet."}</span>;
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-1 text-[13px] text-muted">
      {msg}
      <span>The <b className="text-graphite">physical emergency stop</b> on the machine is the primary stop. It works without this screen, the network or the Pi.</span>
    </div>
  );
}
