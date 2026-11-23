import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend as RechartsLegend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface Slice {
  label: string;
  value: number;
  color: string; // CSS var or hex
}

const TOOLTIP_STYLE = {
  background: "var(--surface-0)",
  border: "1px solid var(--border)",
  borderRadius: "9px",
  fontSize: "12px",
  color: "var(--text-1)",
  boxShadow: "var(--shadow-md)",
};

export function Donut({ data, total, centerLabel }: { data: Slice[]; total?: number; centerLabel?: string }) {
  const sum = total ?? data.reduce((a, s) => a + s.value, 0);
  const slices = data.filter((s) => s.value > 0);
  return (
    <div style={{ position: "relative", width: "100%", height: 180 }}>
      <ResponsiveContainer>
        <PieChart>
          <Pie
            data={slices}
            dataKey="value"
            nameKey="label"
            innerRadius={56}
            outerRadius={80}
            paddingAngle={2}
            stroke="var(--surface-1)"
            strokeWidth={2}
          >
            {slices.map((s, i) => (
              <Cell key={i} fill={s.color} />
            ))}
          </Pie>
          <Tooltip contentStyle={TOOLTIP_STYLE} />
        </PieChart>
      </ResponsiveContainer>
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "grid",
          placeItems: "center",
          pointerEvents: "none",
        }}
      >
        <div style={{ textAlign: "center" }}>
          <div style={{ fontSize: "var(--text-2xl)", fontWeight: 650 }}>{sum}</div>
          {centerLabel && <div className="muted" style={{ fontSize: "var(--text-xs)" }}>{centerLabel}</div>}
        </div>
      </div>
    </div>
  );
}

export function Legend({ data }: { data: Slice[] }) {
  return (
    <div className="col" style={{ gap: 6 }}>
      {data.filter((d) => d.value > 0).map((s) => (
        <div key={s.label} className="row spread" style={{ fontSize: "var(--text-sm)" }}>
          <span className="row" style={{ gap: 7 }}>
            <span className="sev-dot" style={{ background: s.color }} /> {s.label}
          </span>
          <strong>{s.value}</strong>
        </div>
      ))}
    </div>
  );
}

export function MiniBar({ data }: { data: Slice[] }) {
  const d = data.filter((s) => s.value > 0);
  return (
    <div style={{ width: "100%", height: 200 }}>
      <ResponsiveContainer>
        <BarChart data={d} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--text-3)" }} tickLine={false} axisLine={{ stroke: "var(--border)" }} />
          <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "var(--text-3)" }} tickLine={false} axisLine={false} />
          <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "var(--surface-hover)" }} />
          <Bar dataKey="value" radius={[4, 4, 0, 0]}>
            {d.map((s, i) => (
              <Cell key={i} fill={s.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export interface TrendDatum {
  scan_id: number;
  label: string;
  health_score: number | null;
  vulnerable: number | null;
  outdated: number | null;
}

/** Health score over time, with vulnerability and outdated counts overlaid.
 *  Health uses the left 0-100 axis; counts use the right axis. Clicking a point
 *  invokes `onPointClick` with that scan's id. */
export function TrendChart({
  data,
  onPointClick,
}: {
  data: TrendDatum[];
  onPointClick?: (scanId: number) => void;
}) {
  return (
    <div style={{ width: "100%", height: 280, cursor: onPointClick ? "pointer" : "default" }}>
      <ResponsiveContainer>
        <LineChart
          data={data}
          margin={{ top: 8, right: 12, bottom: 4, left: -16 }}
          onClick={(e: { activePayload?: { payload: TrendDatum }[] }) => {
            const point = e?.activePayload?.[0]?.payload;
            if (point && onPointClick) onPointClick(point.scan_id);
          }}
        >
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--text-3)" }} tickLine={false} axisLine={{ stroke: "var(--border)" }} />
          <YAxis yAxisId="score" domain={[0, 100]} tick={{ fontSize: 11, fill: "var(--text-3)" }} tickLine={false} axisLine={false} width={34} />
          <YAxis yAxisId="count" orientation="right" allowDecimals={false} tick={{ fontSize: 11, fill: "var(--text-3)" }} tickLine={false} axisLine={false} width={28} />
          <Tooltip contentStyle={TOOLTIP_STYLE} />
          <RechartsLegend wrapperStyle={{ fontSize: 12 }} />
          <Line yAxisId="score" type="monotone" dataKey="health_score" name="Health" stroke="var(--accent)" strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} connectNulls />
          <Line yAxisId="count" type="monotone" dataKey="vulnerable" name="Vulnerable" stroke="var(--danger)" strokeWidth={1.5} dot={{ r: 2 }} connectNulls />
          <Line yAxisId="count" type="monotone" dataKey="outdated" name="Outdated" stroke="var(--warn)" strokeWidth={1.5} dot={{ r: 2 }} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** A tiny inline health-score line for a project card. Renders nothing with
 *  fewer than two points (a single dot conveys no trend). */
export function Sparkline({ values, color = "var(--accent)" }: { values: number[]; color?: string }) {
  if (!values || values.length < 2) return null;
  const data = values.map((v, i) => ({ i, v }));
  return (
    <div style={{ width: "100%", height: 30 }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 3, right: 3, bottom: 3, left: 3 }}>
          <Line type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export const SEVERITY_COLORS: Record<string, string> = {
  critical: "var(--crit)",
  high: "var(--danger)",
  moderate: "var(--warn)",
  low: "var(--info)",
  unknown: "var(--text-3)",
};

export const LICENSE_COLORS: Record<string, string> = {
  permissive: "var(--ok)",
  "weak-copyleft": "var(--warn)",
  copyleft: "var(--danger)",
  proprietary: "var(--crit)",
  unknown: "var(--text-3)",
  other: "var(--info)",
};
