import type { PiState } from "../types";

/** Side elevation of the arm drawn from the Pi's joint feedback, like a technical drawing.
 *  It is a schematic: link lengths are illustrative and angle conventions follow the Pi config.
 *  Simulated feedback is drawn in dashed plan-blue; real feedback in solid graphite. */

const L = { boom: 128, stick: 92, bucket: 30 };
const PIVOT = { x: 132, y: 150 };
const rad = (d: number) => (d * Math.PI) / 180;

function poseFrom(j: Record<string, number | null>) {
  const boom = j.boom ?? 30, stick = j.stick ?? 45, bucket = j.bucket ?? 40;
  const a1 = rad(boom + 5);                       // boom raised above horizontal
  const p1 = { x: PIVOT.x + L.boom * Math.cos(a1), y: PIVOT.y - L.boom * Math.sin(a1) };
  const a2 = a1 - rad(stick + 70);                // stick folds down from the boom tip
  const p2 = { x: p1.x + L.stick * Math.cos(a2), y: p1.y - L.stick * Math.sin(a2) };
  const a3 = a2 - rad(bucket * 0.9 - 20);         // bucket curls from the stick tip
  const p3 = { x: p2.x + L.bucket * Math.cos(a3), y: p2.y - L.bucket * Math.sin(a3) };
  return { p1, p2, p3, a2, a3 };
}

function Readout({ x, y, name, value, units, active, simulated, anchor = "start" }: {
  x: number; y: number; name: string; value: number | null; units: string | null; active: boolean; simulated: boolean; anchor?: "start" | "end";
}) {
  return (
    <text x={x} y={y} textAnchor={anchor} className="font-display" fontSize={13}>
      <tspan fill="#566173">{name} </tspan>
      <tspan fill={active ? (simulated ? "#2F5EA8" : "#7A5A00") : "#1C2430"} fontWeight={600}>{value == null ? "—" : `${value.toFixed(1)}${units === "deg" ? "°" : ` ${units ?? ""}`}`}</tspan>
    </text>
  );
}

export function ArmDiagram({ pi, movingJoint }: { pi: PiState | null; movingJoint: string | null }) {
  const joints = pi?.joints ?? {};
  const pos = Object.fromEntries(Object.entries(joints).map(([k, v]) => [k, v.position]));
  const has = Object.values(joints).some((j) => j.position != null);
  const simulated = Object.values(joints).some((j) => j.feedback === "simulated");
  const { p1, p2, p3, a3 } = poseFrom(pos);
  const stroke = !has ? "#C9D0D9" : simulated ? "#2F5EA8" : "#1C2430";
  const dash = simulated ? "7 5" : undefined;
  // Machine yellow only marks real hardware motion; a simulated move is drawn solid in dark plan-blue.
  const hl = (j: string) => (movingJoint === j ? (simulated ? "#1E3F73" : "#F2B300") : stroke);
  const cabin = pos.cabin ?? null;
  const units = (k: string) => joints[k]?.units ?? null;

  // Bucket drawn as a small open scoop at the stick tip.
  const b1 = { x: p3.x + 16 * Math.cos(a3 - Math.PI / 2), y: p3.y - 16 * Math.sin(a3 - Math.PI / 2) };

  return (
    <figure className="relative">
      <svg viewBox="0 0 360 200" className="h-auto w-full" role="img"
        aria-label={has ? `Arm pose: boom ${pos.boom ?? "?"}, stick ${pos.stick ?? "?"}, bucket ${pos.bucket ?? "?"}, cabin ${pos.cabin ?? "?"}` : "No joint feedback"}>
        {/* drawing grid */}
        <defs>
          <pattern id="grid" width="12" height="12" patternUnits="userSpaceOnUse">
            <path d="M12 0H0V12" fill="none" stroke="#E6EAEF" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width="360" height="200" fill="url(#grid)" />
        {/* ground */}
        <line x1="10" y1="186" x2="350" y2="186" stroke="#8792A2" strokeWidth="1.5" />
        {Array.from({ length: 22 }, (_, i) => <line key={i} x1={14 + i * 16} y1="186" x2={6 + i * 16} y2="194" stroke="#C9D0D9" />)}
        {/* tracks + cabin */}
        <rect x="60" y="164" width="120" height="20" rx="10" fill="none" stroke={stroke} strokeWidth="2" strokeDasharray={dash} />
        <rect x="88" y="128" width="64" height="34" rx="3" fill="#fff" stroke={hl("cabin")} strokeWidth="2" strokeDasharray={dash} />
        <rect x="96" y="134" width="22" height="16" fill="none" stroke={stroke} strokeWidth="1" opacity=".6" />
        {/* boom, stick, bucket */}
        <line x1={PIVOT.x} y1={PIVOT.y} x2={p1.x} y2={p1.y} stroke={hl("boom")} strokeWidth="7" strokeLinecap="round" strokeDasharray={dash} />
        <line x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y} stroke={hl("stick")} strokeWidth="5" strokeLinecap="round" strokeDasharray={dash} />
        <path d={`M${p2.x},${p2.y} L${p3.x},${p3.y} Q${(p3.x + b1.x) / 2 + 6},${(p3.y + b1.y) / 2 + 6} ${b1.x},${b1.y}`}
          fill="none" stroke={hl("bucket")} strokeWidth="4" strokeLinejoin="round" strokeDasharray={dash} />
        {[PIVOT, p1, p2].map((p, i) => <circle key={i} cx={p.x} cy={p.y} r="4" fill="#fff" stroke={stroke} strokeWidth="2" />)}
        {/* readouts placed like dimension callouts */}
        <Readout x={Math.min(PIVOT.x + (p1.x - PIVOT.x) / 2 - 8, 200)} y={Math.min((PIVOT.y + p1.y) / 2, 150) - 10} name="Boom" value={pos.boom ?? null} units={units("boom")} active={movingJoint === "boom"} simulated={simulated} anchor="end" />
        <Readout x={Math.min(p1.x + 10, 270)} y={Math.max(p1.y - 6, 16)} name="Stick" value={pos.stick ?? null} units={units("stick")} active={movingJoint === "stick"} simulated={simulated} />
        <Readout x={Math.min(p2.x + 12, 270)} y={Math.min(p2.y + 22, 178)} name="Bucket" value={pos.bucket ?? null} units={units("bucket")} active={movingJoint === "bucket"} simulated={simulated} />
        {/* cabin rotation dial (top view) */}
        <g transform="translate(40,40)">
          <circle r="24" fill="#fff" stroke="#C9D0D9" strokeWidth="1.5" />
          <line x1="0" y1="0" x2="0" y2="-24" stroke="#D5DBE2" strokeDasharray="2 3" />
          {cabin != null && (
            <line x1="0" y1="0" x2={22 * Math.sin(rad(cabin))} y2={-22 * Math.cos(rad(cabin))}
              stroke={hl("cabin")} strokeWidth="3" strokeLinecap="round" />
          )}
          <circle r="3" fill={stroke} />
          <text y="40" textAnchor="middle" fontSize="12" className="font-display">
            <tspan fill="#566173">Cabin </tspan>
            <tspan fill={movingJoint === "cabin" ? (simulated ? "#2F5EA8" : "#7A5A00") : "#1C2430"} fontWeight={600}>{cabin == null ? "—" : `${cabin.toFixed(1)}°`}</tspan>
          </text>
        </g>
      </svg>
      <figcaption className="mt-1 flex flex-wrap items-center justify-between gap-2 text-[12px] text-muted">
        <span>
          {!has ? "No joint feedback from the Pi." : simulated
            ? <><span className="font-semibold text-accent">Simulated feedback.</span> No motor is driven.</>
            : "Live feedback from the Pi."}
        </span>
        <span>Schematic, not to scale</span>
      </figcaption>
    </figure>
  );
}
