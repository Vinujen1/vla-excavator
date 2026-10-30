import { useState } from "react";
import { fmtTime } from "../api";
import type { LogEvent } from "../types";
import { Segmented } from "../ui";

const levelClass = { info: "text-graphite", warn: "text-warn", error: "text-fault" };

export function EventLog({ log }: { log: LogEvent[] }) {
  const [filter, setFilter] = useState<"all" | "issues">("all");
  const shown = (filter === "all" ? log : log.filter((e) => e.level !== "info")).slice().reverse();
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <Segmented label="Filter events" value={filter} onChange={setFilter}
          options={[{ id: "all", label: "Everything" }, { id: "issues", label: "Warnings and faults" }]} />
        <a href="/api/logs/session" download className="text-[14px] font-semibold text-accent hover:underline">Download this session's log</a>
      </div>
      <div className="h-72 overflow-auto rounded-md border border-rule bg-paper/60 font-mono text-[12.5px] leading-relaxed">
        {shown.length === 0 && <p className="p-3 font-sans text-muted">Nothing logged yet.</p>}
        {shown.map((e) => (
          <div key={e.seq} className="flex gap-3 border-b border-rule/60 px-3 py-1">
            <span className="shrink-0 text-faint">{fmtTime(e.time)}</span>
            <span className="w-24 shrink-0 text-accent">{e.source}</span>
            <span className={levelClass[e.level]}>{e.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
