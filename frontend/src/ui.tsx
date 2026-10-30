import { useState, type ReactNode } from "react";

/** A white work sheet. Sections are separated by hairline rules, not stacked shadow cards. */
export function Card({ title, right, children, className = "", flush = false }: {
  title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; flush?: boolean;
}) {
  return (
    <section className={`rounded-[10px] border border-rule bg-sheet ${className}`}>
      {title !== undefined && (
        <header className="flex min-h-[44px] items-center justify-between gap-3 border-b border-rule px-4 py-2">
          <h2 className="font-display text-[17px] font-semibold text-graphite">{title}</h2>
          {right}
        </header>
      )}
      <div className={flush ? "" : "p-4"}>{children}</div>
    </section>
  );
}

export type Tone = "ok" | "accent" | "warn" | "fault" | "muted" | "machine";

const toneClass: Record<Tone, string> = {
  ok: "border-ok/30 bg-ok-wash text-ok",
  accent: "border-accent/30 bg-accent-wash text-accent",
  warn: "border-warn/30 bg-warn-wash text-warn",
  fault: "border-fault/30 bg-fault-wash text-fault",
  muted: "border-rule bg-paper text-muted",
  machine: "border-machine bg-machine-wash text-machine-ink",
};

const dotClass: Record<Tone, string> = {
  ok: "bg-ok", accent: "bg-accent", warn: "bg-warn", fault: "bg-fault", muted: "bg-faint", machine: "bg-machine",
};

export function Badge({ tone, children, pulse = false }: { tone: Tone; children: ReactNode; pulse?: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[13px] font-medium ${toneClass[tone]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${dotClass[tone]} ${pulse ? "animate-beacon" : ""}`} />
      {children}
    </span>
  );
}

/** A status light with a plain label, for the connection row in the top bar. */
export function Light({ tone, label, detail }: { tone: Tone; label: string; detail?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-[13px] text-muted" title={detail}>
      <span className={`h-2.5 w-2.5 rounded-full ring-2 ring-white ${dotClass[tone]}`} />
      <span className="text-graphite">{label}</span>
      {detail && <span className="hidden xl:inline">{detail}</span>}
    </span>
  );
}

export function Stat({ label, value, unit, tone }: { label: string; value: ReactNode; unit?: string; tone?: Tone }) {
  const color = tone === "warn" ? "text-warn" : tone === "fault" ? "text-fault" : tone === "muted" ? "text-faint" : "text-graphite";
  return (
    <div className="min-w-0">
      <div className="text-[12px] text-muted">{label}</div>
      <div className={`font-display text-[19px] font-semibold tabular-nums leading-tight ${color}`}>
        {value}
        {unit && <span className="ml-1 font-sans text-[12px] font-normal text-faint">{unit}</span>}
      </div>
    </div>
  );
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-rule/70 py-1.5 last:border-0">
      <span className="shrink-0 text-[13px] text-muted">{label}</span>
      <span className="min-w-0 text-right text-[14px] tabular-nums text-graphite">{children}</span>
    </div>
  );
}

export function Button({ children, onClick, disabled, variant = "secondary", title, type = "button", size = "md" }: {
  children: ReactNode; onClick?: () => void; disabled?: boolean; variant?: "primary" | "secondary" | "danger" | "ghost";
  title?: string; type?: "button" | "submit"; size?: "md" | "lg";
}) {
  const styles = {
    primary: "bg-accent text-white hover:bg-accent-dim disabled:bg-rule disabled:text-faint",
    secondary: "border border-rule bg-sheet text-graphite hover:border-accent hover:text-accent disabled:text-faint disabled:hover:border-rule",
    danger: "bg-fault text-white hover:bg-[#B9261B] disabled:bg-rule disabled:text-faint",
    ghost: "text-accent hover:bg-accent-wash disabled:text-faint",
  }[variant];
  const sz = size === "lg" ? "px-4 py-2 text-[15px]" : "px-3 py-1.5 text-[14px]";
  return (
    <button type={type} title={title} onClick={onClick} disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 rounded-md font-semibold transition-colors duration-150 disabled:cursor-not-allowed ${sz} ${styles}`}>
      {children}
    </button>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="inline-flex cursor-pointer select-none items-center gap-2 text-[14px] text-graphite">
      <span role="switch" aria-checked={checked} aria-label={label} tabIndex={0}
        onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); onChange(!checked); } }}
        onClick={() => onChange(!checked)}
        className={`relative h-5 w-9 rounded-full transition-colors duration-200 ${checked ? "bg-accent" : "bg-rule"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 ${checked ? "translate-x-4" : "translate-x-0.5"}`} />
      </span>
      {label}
    </label>
  );
}

export function Note({ tone = "muted", children }: { tone?: Tone; children: ReactNode }) {
  const c = {
    warn: "border-warn/25 bg-warn-wash text-[#7A3A06]",
    fault: "border-fault/25 bg-fault-wash text-[#8E1C13]",
    accent: "border-accent/20 bg-accent-wash text-[#1E3F73]",
    ok: "border-ok/25 bg-ok-wash text-[#17573A]",
    machine: "border-machine/40 bg-machine-wash text-machine-ink",
    muted: "border-rule bg-paper text-muted",
  }[tone];
  return <div className={`rounded-md border px-3 py-2 text-[13px] leading-relaxed ${c}`}>{children}</div>;
}

/** Segmented control (used for view switches and the bottom tray). */
export function Segmented<T extends string>({ value, onChange, options, label }: {
  value: T; onChange: (v: T) => void; options: { id: T; label: ReactNode; disabled?: boolean }[]; label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="inline-flex rounded-md border border-rule bg-paper p-0.5 text-[13px]">
      {options.map((o) => (
        <button key={o.id} role="tab" aria-selected={value === o.id} disabled={o.disabled} onClick={() => onChange(o.id)}
          className={`rounded px-3 py-1 font-medium transition-colors disabled:cursor-not-allowed disabled:text-faint ${value === o.id ? "bg-sheet text-graphite shadow-sm" : "text-muted hover:text-graphite"}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Tabbed tray: secondary panels share one sheet so the main screen stays focused. */
export function Tray({ tabs, initial }: { tabs: { id: string; label: ReactNode; body: ReactNode }[]; initial?: string }) {
  const [active, setActive] = useState(() => {
    try { return localStorage.getItem("tray") ?? initial ?? tabs[0].id; } catch { return initial ?? tabs[0].id; }
  });
  const choose = (id: string) => { setActive(id); try { localStorage.setItem("tray", id); } catch { /* storage unavailable */ } };
  const current = tabs.find((t) => t.id === active) ?? tabs[0];
  return (
    <section className="rounded-[10px] border border-rule bg-sheet">
      <div role="tablist" aria-label="More panels" className="flex gap-1 overflow-x-auto border-b border-rule px-2">
        {tabs.map((t) => (
          <button key={t.id} role="tab" aria-selected={t.id === current.id} onClick={() => choose(t.id)}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-[14px] font-medium transition-colors ${t.id === current.id ? "border-accent text-graphite" : "border-transparent text-muted hover:text-graphite"}`}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="p-4" role="tabpanel">{current.body}</div>
    </section>
  );
}
