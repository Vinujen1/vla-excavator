import { useEffect, useRef, useState } from "react";
import { api, fmtTime } from "../api";
import type { AudioDevice, CommandResult, VoiceState } from "../types";
import { Badge, Button, Card, Note } from "../ui";

export interface InstructionDraft {
  text: string;
  source: "typed" | "jetson_mic" | "browser_mic";
  recording?: string;
  audio_device?: string;
  transcribed_at?: number;
}

type Mode = "typed" | "jetson_mic" | "browser_mic";

export function CommandPanel({ voice, draft, setDraft, provider, lastCommand }: {
  voice: VoiceState | undefined; draft: InstructionDraft; setDraft: (d: InstructionDraft) => void;
  provider: string; lastCommand: CommandResult | null;
}) {
  const [sending, setSending] = useState(false);
  const [mode, setMode] = useState<Mode>("typed");
  const [devices, setDevices] = useState<AudioDevice[]>([]);
  const [device, setDevice] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [browserRec, setBrowserRec] = useState<{ rec: MediaRecorder; started: number } | null>(null);
  const [now, setNow] = useState(Date.now());
  const lastTranscript = useRef<number | null>(null);

  const loadDevices = () =>
    api<AudioDevice[]>("/api/audio/devices").then((d) => {
      setDevices(d);
      setDevice((cur) => (d.some((x) => x.id === cur) ? cur : (d.find((x) => x.is_dji) ?? d[0])?.id ?? ""));
    }).catch((e: Error) => setError(e.message));
  useEffect(() => { loadDevices(); }, []);

  useEffect(() => {
    if (!browserRec) return;
    const t = window.setInterval(() => setNow(Date.now()), 200);
    return () => window.clearInterval(t);
  }, [browserRec]);

  // A new transcript fills the instruction box, tagged with its real source.
  useEffect(() => {
    const t = voice?.transcript;
    if (!t || t.transcribed_at_unix === lastTranscript.current) return;
    lastTranscript.current = t.transcribed_at_unix;
    if (t.text) {
      setDraft({ text: t.text, source: t.source as InstructionDraft["source"], recording: t.recording ?? undefined,
        audio_device: voice?.source_label ?? undefined, transcribed_at: t.transcribed_at_unix });
    }
  }, [voice?.transcript, voice?.source_label, setDraft]);

  const busy = voice?.state === "recording" || voice?.state === "transcribing" || voice?.state === "listening";
  const listening = voice?.state === "listening";
  const jetsonRecording = voice?.state === "recording" && voice.source === "jetson_mic";
  const hasDji = devices.some((d) => d.is_dji);

  const startJetson = () => api("/api/voice/jetson/start", { method: "POST", json: { device } }).then(() => setError(null)).catch((e: Error) => setError(e.message));
  const stopJetson = () => api("/api/voice/jetson/stop", { method: "POST" }).catch((e: Error) => setError(e.message));
  const startListening = () => api("/api/voice/listen/start", { method: "POST", json: { device } }).then(() => setError(null)).catch((e: Error) => setError(e.message));
  const stopListening = () => api("/api/voice/listen/stop", { method: "POST" }).catch((e: Error) => setError(e.message));

  // Every command (typed or a confirmed transcript) goes through the same backend pipeline.
  const send = async () => {
    setSending(true); setError(null);
    try {
      await api("/api/command", { method: "POST", json: { text: draft.text.trim(), source: draft.source, provider,
        recording: draft.recording ?? null, transcribed_at: draft.transcribed_at ?? null } });
    } catch (e) { setError((e as Error).message); }
    setSending(false);
  };

  const startBrowser = async () => {
    setError(null);
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("This browser can't access a microphone here (it needs a secure context: open the page via http://localhost through the tunnel).");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      const rec = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
        try {
          await fetch("/api/voice/browser", { method: "POST", body: blob, headers: { "Content-Type": blob.type } })
            .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText); });
        } catch (e) { setError((e as Error).message); }
      };
      rec.start();
      setBrowserRec({ rec, started: Date.now() });
    } catch (e) {
      setError(`Laptop microphone unavailable: ${(e as Error).message}`);
    }
  };
  const stopBrowser = () => { browserRec?.rec.stop(); setBrowserRec(null); };

  const sourceBadge = draft.source === "typed" ? <Badge tone="muted">Typed</Badge>
    : draft.source === "jetson_mic" ? <Badge tone="accent">Voice · Jetson mic</Badge> : <Badge tone="accent">Voice · laptop browser mic</Badge>;

  return (
    <Card title="Operator command" right={sourceBadge}>
      <div role="tablist" aria-label="Command source" className="mb-3 flex w-full rounded-md border border-rule bg-paper p-0.5 text-[13px]">
        {([["typed", "Type"], ["jetson_mic", "Jetson mic"], ["browser_mic", "Laptop mic"]] as const).map(([m, label]) => (
          <button key={m} onClick={() => setMode(m)} disabled={busy || !!browserRec}
            role="tab" aria-selected={mode === m} className={`flex-1 rounded px-3 py-1.5 font-medium transition-colors disabled:cursor-not-allowed ${mode === m ? "bg-sheet text-graphite shadow-sm" : "text-muted hover:text-graphite"}`}>
            {label}
          </button>
        ))}
      </div>

      {mode === "jetson_mic" && (
        <div className="mb-3 space-y-2">
          <div className="flex gap-2">
            <select value={device} onChange={(e) => setDevice(e.target.value)} disabled={busy}
              className="min-w-0 flex-1 rounded-lg border border-rule bg-sheet px-2 py-1.5 text-sm text-graphite">
              {devices.map((d) => <option key={d.id} value={d.id}>{d.is_dji ? "DJI · " : ""}{d.label}</option>)}
            </select>
            <Button onClick={loadDevices} disabled={busy} title="Refresh Jetson audio devices">↻</Button>
          </div>
          {!hasDji && <Note tone="warn">No DJI receiver detected on the Jetson. Recording uses the device selected above, which is <b>not</b> the DJI mic.</Note>}
          <div className="flex flex-wrap gap-2">
            <Button variant={jetsonRecording ? "danger" : "primary"} disabled={!device || (busy && !jetsonRecording)}
              onClick={jetsonRecording ? stopJetson : startJetson}>
              {jetsonRecording ? `■ Stop & transcribe (${voice?.recording_elapsed_s ?? 0}s / ${voice?.max_record_s}s)` : "● Push to talk"}
            </Button>
            <Button variant={listening ? "danger" : "secondary"} disabled={!device || (busy && !listening)}
              onClick={listening ? stopListening : startListening}
              title="Records short chunks continuously for a bounded time; only stop/pause/cancel run without confirmation">
              {listening ? `■ Stop listening (${voice?.listening?.elapsed_s ?? 0}s / ${voice?.listening?.max_s}s)` : "Listening mode"}
            </Button>
          </div>
        </div>
      )}

      {mode === "browser_mic" && (
        <div className="mb-3 space-y-2">
          <Note>Records on <b>this laptop</b> through the browser and uploads the audio to the Jetson for transcription. It doesn't use the Jetson's DJI receiver.</Note>
          <Button variant={browserRec ? "danger" : "primary"} disabled={busy && !browserRec} onClick={browserRec ? stopBrowser : startBrowser}>
            {browserRec ? `■ Stop & transcribe (${Math.floor((now - browserRec.started) / 1000)}s)` : "● Push to record on laptop"}
          </Button>
        </div>
      )}

      {voice && voice.state !== "idle" && (
        <div className="mb-3">
          {voice.state === "recording" && <Badge tone="fault" pulse>Recording · {voice.source_label}</Badge>}
          {voice.state === "listening" && <Badge tone="fault" pulse>Listening · {voice.listening?.device} · {voice.listening?.chunks} chunk(s)</Badge>}
          {voice.state === "transcribing" && <Badge tone="accent" pulse>Transcribing with Whisper {voice.whisper_model}…</Badge>}
          {voice.state === "error" && <Note tone="fault">{voice.error}</Note>}
        </div>
      )}

      <label className="text-[12px] text-muted">Instruction</label>
      <textarea value={draft.text} rows={3} maxLength={500}
        onChange={(e) => setDraft({ text: e.target.value, source: "typed" })}
        className="mt-1 w-full resize-y rounded-lg border border-rule bg-sheet px-3 py-2 text-sm text-graphite outline-none transition-colors focus:border-accent" />

      {voice?.transcript && (
        <div className="mt-2 text-xs text-muted">
          Last transcript ({fmtTime(voice.transcript.transcribed_at_unix)}, {voice.transcript.source_label}, {voice.transcript.processing_s}s):{" "}
          <span className="text-graphite">{voice.transcript.text ? `"${voice.transcript.text}"` : "no speech detected"}</span>
          {voice.transcript.low_confidence && <span className="ml-1 text-warn">(low recognition confidence: check before sending)</span>}
          {voice.transcript.recording && (
            <audio controls src={`/recordings/${voice.transcript.recording}`} className="mt-1 h-8 w-full" />
          )}
        </div>
      )}
      {draft.source === "typed" && voice?.transcript?.text && draft.text !== voice.transcript.text && (
        <p className="mt-1 text-[12px] text-faint">Edited text is sent as a typed instruction.</p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={send} disabled={sending || !draft.text.trim()}>{sending ? "Sending…" : "Send command"}</Button>
        <span className="text-[12px] text-faint">Stop · pause · resume · identify the red marker · plan / start scoop-and-dump for N cycles</span>
      </div>
      {lastCommand && (
        <div className="mt-2">
          <Note tone={lastCommand.handled ? "accent" : lastCommand.needs_confirmation ? "warn" : "fault"}>
            <span className="text-muted">{fmtTime(lastCommand.at)} · {lastCommand.source.replace("_", " ")} · {lastCommand.kind}:</span> {lastCommand.message}
          </Note>
        </div>
      )}
      {error && <div className="mt-2"><Note tone="fault">{error}</Note></div>}
    </Card>
  );
}
