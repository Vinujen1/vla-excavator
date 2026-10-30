import { useEffect, useRef, useState } from "react";
import type { LogEvent, Telemetry } from "./types";

export async function api<T = unknown>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(path, {
    ...rest,
    ...(json !== undefined ? { body: JSON.stringify(json), headers: { "Content-Type": "application/json" } } : {}),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (body as { detail?: unknown }).detail;
    throw new Error(typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : res.statusText);
  }
  return body as T;
}

export type LinkState = "connecting" | "live" | "lost";

/** Telemetry over WebSocket with automatic reconnect. Events accumulate client-side (bounded). */
export function useTelemetry() {
  const [data, setData] = useState<Telemetry | null>(null);
  const [link, setLink] = useState<LinkState>("connecting");
  const [log, setLog] = useState<LogEvent[]>([]);
  const lastMessage = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry: number | undefined;
    let closed = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/telemetry`);
      ws.onopen = () => setLink("live");
      ws.onmessage = (msg) => {
        const t = JSON.parse(msg.data) as Telemetry;
        lastMessage.current = Date.now();
        setLink("live");  // messages flowing again clears a watchdog "lost"
        setData(t);
        if (t.events.length) {
          setLog((prev) => {
            const seen = new Set(prev.map((e) => e.seq));
            return [...prev, ...t.events.filter((e) => !seen.has(e.seq))].slice(-300);
          });
        }
      };
      ws.onclose = () => {
        setLink("lost");
        if (!closed) retry = window.setTimeout(connect, 1500);
      };
      ws.onerror = () => ws?.close();
    };
    connect();

    // A silent socket (e.g. a stalled SSH tunnel) counts as lost too.
    const watchdog = window.setInterval(() => {
      if (lastMessage.current && Date.now() - lastMessage.current > 5000) setLink("lost");
    }, 1000);

    return () => {
      closed = true;
      window.clearTimeout(retry);
      window.clearInterval(watchdog);
      ws?.close();
    };
  }, []);

  return { data, link, log };
}

export const fmtTime = (t?: number | null) =>
  t ? new Date(t * 1000).toLocaleTimeString([], { hour12: false }) + "." + String(Math.floor((t % 1) * 1000)).padStart(3, "0") : "—";

export const fmtNum = (v: number | null | undefined, digits = 2, unit = "") =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : `${v.toFixed(digits)}${unit}`;
