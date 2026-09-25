// Stats tab: one /stats call per time window, drawn as a KPI row followed by
// the views that answer "is the channel healthy, and why did things not
// post": activity over time, where items end up, which sources earn their
// place, what the market-impact scorer is doing, when posts go out, and the
// two filters worth auditing (the editor and the dedup ladder).
"use client";

import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useStats } from "@/hooks/useApi";
import { STATUS_INFO, statusInfo } from "@/lib/status";
import type { ActivityPoint, StatsRange, StatsResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

const RANGES: { id: StatsRange; label: string; long: string }[] = [
  { id: "24h", label: "24h", long: "last 24 hours" },
  { id: "7d", label: "7d", long: "last 7 days" },
  { id: "30d", label: "30d", long: "last 30 days" },
  { id: "all", label: "All", long: "all time" },
];

const RANGE_KEY = "market-one-stats-range";

// Two series, one unit (items), so one axis. "Arrived" is a calm blue;
// "Published" wears the published status green used everywhere else.
const ARRIVED = "#3b82f6";
const PUBLISHED = STATUS_INFO.published.color;

const TOOLTIP_STYLE = {
  background: "var(--surface-primary)",
  border: "1px solid var(--border-color)",
  borderRadius: 8,
  fontSize: 12,
  color: "var(--ink-primary)",
};

const nf = new Intl.NumberFormat();

function pct(n: number | null | undefined, digits = 0) {
  if (n == null || Number.isNaN(n)) return "–";
  return `${(n * 100).toFixed(digits)}%`;
}

function formatDuration(minutes: number | null) {
  if (minutes == null) return "–";
  if (minutes < 1) return "<1 min";
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return m ? `${h}h ${m}m` : `${h}h`;
}

function parseUtc(value: string) {
  // "YYYY-MM-DD HH:MM[:SS]" or "YYYY-MM-DD", stored as naive UTC.
  const iso = value.length === 10 ? `${value}T00:00:00Z` : `${value.replace(" ", "T")}${value.length === 16 ? ":00" : ""}Z`;
  return new Date(iso);
}

function timeAgo(value: string | null) {
  if (!value) return "never";
  const date = parseUtc(value);
  if (Number.isNaN(date.getTime())) return value;
  const minutes = Math.round((Date.now() - date.getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)}h ago`;
  return `${Math.round(minutes / 1440)}d ago`;
}

// The API only returns buckets that had something in them; a chart of
// those alone would silently squeeze out the quiet hours. Fill the gaps.
function fillActivity(points: ActivityPoint[], range: StatsRange): ActivityPoint[] {
  const byKey = new Map(points.map((p) => [p.bucket, p]));
  const hourly = range === "24h";
  const step = hourly ? 3_600_000 : 86_400_000;
  const keyOf = (d: Date) =>
    hourly ? `${d.toISOString().slice(0, 13).replace("T", " ")}:00` : d.toISOString().slice(0, 10);

  const now = new Date();
  let start: Date;
  if (range === "all") {
    if (!points.length) return [];
    start = parseUtc(points[0].bucket);
  } else {
    const count = range === "24h" ? 24 : range === "7d" ? 7 : 30;
    start = new Date(now.getTime() - (count - 1) * step);
  }
  const out: ActivityPoint[] = [];
  for (let t = start.getTime(); t <= now.getTime() + 1; t += step) {
    const key = keyOf(new Date(t));
    out.push(byKey.get(key) ?? { bucket: key, ingested: 0, published: 0 });
  }
  return out;
}

function bucketLabel(bucket: string, hourly: boolean) {
  const d = parseUtc(bucket);
  if (Number.isNaN(d.getTime())) return bucket;
  return hourly
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { month: "short", day: "numeric" });
}

// ── small building blocks ────────────────────────────────────────────────

function Delta({ now, before }: { now: number; before: number | undefined }) {
  if (before == null) return null;
  if (before === 0) {
    return now === 0 ? null : <span className="text-xs text-ink-muted">new vs previous</span>;
  }
  const change = (now - before) / before;
  const up = change >= 0;
  return (
    <span className="inline-flex items-center gap-0.5 text-xs text-ink-muted" title="Compared with the previous window of the same length">
      {up ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
      {pct(Math.abs(change))} vs previous
    </span>
  );
}

function Kpi({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  accent?: string;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-surface-primary p-4 shadow-sm">
      <span className="flex items-center gap-1.5 text-xs font-medium text-ink-muted">
        {accent && <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: accent }} />}
        {label}
      </span>
      <span className="text-2xl font-semibold tabular-nums tracking-tight text-ink-primary">{value}</span>
      {sub && <span className="min-h-[1rem]">{sub}</span>}
    </div>
  );
}

// A thin horizontal meter for inside tables and lists.
function Meter({ value, max, color }: { value: number; max: number; color: string }) {
  const width = max > 0 ? Math.max(value > 0 ? 2 : 0, (value / max) * 100) : 0;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-secondary">
      <div className="h-full rounded-full" style={{ width: `${width}%`, background: color }} />
    </div>
  );
}

function Section({
  title,
  description,
  children,
  className,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Card className={className}>
      <CardHeader className="pb-3">
        <CardTitle>{title}</CardTitle>
        {description && <p className="text-xs text-ink-muted">{description}</p>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function Empty({ children = "Nothing in this window." }: { children?: React.ReactNode }) {
  return <p className="py-6 text-center text-sm text-ink-muted">{children}</p>;
}

// ── the views ─────────────────────────────────────────────────────────────

function ActivityChart({ data, range }: { data: StatsResponse; range: StatsRange }) {
  const hourly = range === "24h";
  const points = fillActivity(data.activity, range).map((p) => ({
    ...p,
    label: bucketLabel(p.bucket, hourly),
  }));
  if (!points.length) return <Empty />;
  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={points} barGap={2} barCategoryGap={points.length > 20 ? "15%" : "25%"} margin={{ left: -16, right: 4 }}>
          <CartesianGrid stroke="var(--border-color)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="label"
            stroke="var(--ink-muted)"
            fontSize={11}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
            minTickGap={16}
          />
          <YAxis allowDecimals={false} stroke="var(--ink-muted)" fontSize={11} tickLine={false} axisLine={false} />
          <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "var(--surface-secondary)" }} />
          <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12, color: "var(--ink-muted)" }} />
          <Bar isAnimationActive={false} dataKey="ingested" name="Arrived" fill={ARRIVED} radius={[3, 3, 0, 0]} />
          <Bar isAnimationActive={false} dataKey="published" name="Published" fill={PUBLISHED} radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function OutcomeBreakdown({ data }: { data: StatsResponse }) {
  const rows = data.status_breakdown;
  const total = rows.reduce((n, r) => n + r.count, 0);
  if (!total) return <Empty />;
  return (
    <div className="flex flex-col gap-4">
      {/* One 100% bar: the whole window at a glance. 2px gaps separate the
          segments so neighbours never blur together. */}
      <div className="flex h-3 w-full gap-[2px] overflow-hidden rounded-full" role="img" aria-label="Share of items by outcome">
        {rows.map((r) => (
          <div
            key={r.status}
            title={`${statusInfo(r.status).label}: ${nf.format(r.count)} (${pct(r.count / total, 1)})`}
            style={{ width: `${(r.count / total) * 100}%`, background: statusInfo(r.status).color }}
          />
        ))}
      </div>
      <ul className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2">
        {rows.map((r) => {
          const info = statusInfo(r.status);
          return (
            <li key={r.status} className="flex flex-col gap-1" title={info.hint}>
              <div className="flex items-baseline justify-between gap-2 text-sm">
                <span className="flex items-center gap-2 text-ink-primary">
                  <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: info.color }} />
                  {info.label}
                </span>
                <span className="tabular-nums text-ink-muted">
                  <span className="font-medium text-ink-primary">{nf.format(r.count)}</span> · {pct(r.count / total, 1)}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function SourcesTable({ data }: { data: StatsResponse }) {
  const rows = data.sources;
  if (!rows.length) return <Empty />;
  const maxRate = Math.max(...rows.map((r) => (r.total ? r.published / r.total : 0)), 0.0001);
  return (
    <div className="table-scroll -mx-4 px-4">
      <table className="w-full min-w-[560px] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-ink-muted">
            <th className="py-2 pr-3 font-medium">Source</th>
            <th className="py-2 pr-3 text-right font-medium">Arrived</th>
            <th className="py-2 pr-3 text-right font-medium">Published</th>
            <th className="w-40 py-2 pr-3 font-medium">Publish rate</th>
            <th className="py-2 pr-3 text-right font-medium" title="Marked duplicate">Dupes</th>
            <th className="py-2 pr-3 text-right font-medium" title="Low impact or irrelevant">Filtered</th>
            <th className="py-2 text-right font-medium" title="Average market impact score, 1–5">Impact</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const rate = r.total ? r.published / r.total : 0;
            return (
              <tr key={r.source_name} className="border-b border-border last:border-0">
                <td className="max-w-[12rem] truncate py-2.5 pr-3 font-medium text-ink-primary" title={r.source_name}>
                  {r.source_name}
                </td>
                <td className="py-2.5 pr-3 text-right tabular-nums">{nf.format(r.total)}</td>
                <td className="py-2.5 pr-3 text-right tabular-nums">{nf.format(r.published ?? 0)}</td>
                <td className="py-2.5 pr-3">
                  <div className="flex items-center gap-2">
                    <Meter value={rate} max={maxRate} color={PUBLISHED} />
                    <span className="w-10 shrink-0 text-right text-xs tabular-nums text-ink-muted">{pct(rate)}</span>
                  </div>
                </td>
                <td className="py-2.5 pr-3 text-right tabular-nums text-ink-muted">{pct(r.total ? r.duplicate / r.total : 0)}</td>
                <td className="py-2.5 pr-3 text-right tabular-nums text-ink-muted">{pct(r.total ? r.filtered / r.total : 0)}</td>
                <td className="py-2.5 text-right tabular-nums text-ink-muted">{r.avg_importance?.toFixed(1) ?? "–"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ImportanceChart({ data }: { data: StatsResponse }) {
  const byScore = new Map(data.importance.map((r) => [r.importance, r]));
  const points = [1, 2, 3, 4, 5].map((score) => {
    const row = byScore.get(score);
    const published = row?.published ?? 0;
    return { score: String(score), published, other: (row?.count ?? 0) - published };
  });
  if (!points.some((p) => p.published || p.other)) return <Empty>No scored items in this window.</Empty>;
  return (
    <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={points} margin={{ left: -16, right: 4 }}>
          <CartesianGrid stroke="var(--border-color)" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="score" stroke="var(--ink-muted)" fontSize={11} tickLine={false} axisLine={false} />
          <YAxis allowDecimals={false} stroke="var(--ink-muted)" fontSize={11} tickLine={false} axisLine={false} />
          <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "var(--surface-secondary)" }} labelFormatter={(v) => `Impact ${v}`} />
          <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12, color: "var(--ink-muted)" }} />
          <Bar isAnimationActive={false} dataKey="published" name="Published" stackId="a" fill={PUBLISHED} />
          <Bar isAnimationActive={false} dataKey="other" name="Not published" stackId="a" fill="#94a3b8" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function RankedList({
  rows,
  color,
  empty,
}: {
  rows: { key: string; label: string; value: number; sub?: string }[];
  color: string;
  empty?: string;
}) {
  if (!rows.length) return <Empty>{empty}</Empty>;
  const max = Math.max(...rows.map((r) => r.value), 1);
  return (
    <ul className="flex flex-col gap-3">
      {rows.map((r) => (
        <li key={r.key} className="flex flex-col gap-1.5">
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="min-w-0 truncate text-ink-primary" title={r.label}>
              {r.label}
            </span>
            <span className="shrink-0 tabular-nums text-ink-muted">
              <span className="font-medium text-ink-primary">{nf.format(r.value)}</span>
              {r.sub && <> · {r.sub}</>}
            </span>
          </div>
          <Meter value={r.value} max={max} color={color} />
        </li>
      ))}
    </ul>
  );
}

function HourOfDay({ data }: { data: StatsResponse }) {
  // Stored hours are UTC; show them in the viewer's own clock.
  const offsetMinutes = -new Date().getTimezoneOffset();
  const local = new Array(24).fill(0) as number[];
  for (const { hour, count } of data.hour_of_day) {
    const h = (((hour * 60 + offsetMinutes) / 60) % 24 + 24) % 24;
    local[Math.floor(h)] += count;
  }
  const points = local.map((count, hour) => ({ hour: `${String(hour).padStart(2, "0")}h`, count }));
  if (!local.some(Boolean)) return <Empty>No posts in this window.</Empty>;
  return (
    <div className="h-48 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={points} barCategoryGap="12%" margin={{ left: -16, right: 4 }}>
          <CartesianGrid stroke="var(--border-color)" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="hour" stroke="var(--ink-muted)" fontSize={11} tickLine={false} axisLine={false} interval={2} />
          <YAxis allowDecimals={false} stroke="var(--ink-muted)" fontSize={11} tickLine={false} axisLine={false} />
          <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "var(--surface-secondary)" }} />
          <Bar isAnimationActive={false} dataKey="count" name="Posts" fill={PUBLISHED} radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function EditorView({ data }: { data: StatsResponse }) {
  const { approve, decline, top_rules } = data.editor;
  const total = approve + decline;
  if (!total) return <Empty>The editor gave no verdicts in this window.</Empty>;
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-3 gap-3 text-center">
        <div>
          <p className="text-xl font-semibold tabular-nums text-ink-primary">{nf.format(approve)}</p>
          <p className="text-xs text-ink-muted">approved</p>
        </div>
        <div>
          <p className="text-xl font-semibold tabular-nums text-ink-primary">{nf.format(decline)}</p>
          <p className="text-xs text-ink-muted">declined</p>
        </div>
        <div>
          <p className="text-xl font-semibold tabular-nums text-ink-primary">{pct(approve / total)}</p>
          <p className="text-xs text-ink-muted">approval rate</p>
        </div>
      </div>
      <div>
        <p className="mb-2 text-xs font-medium text-ink-muted">Rules most often cited in a decline</p>
        <RankedList
          rows={top_rules.map((r) => ({ key: r.rule, label: r.rule, value: r.count }))}
          color={STATUS_INFO.irrelevant.color}
          empty="No rules cited."
        />
      </div>
    </div>
  );
}

function DedupView({ data }: { data: StatsResponse }) {
  return (
    <RankedList
      rows={data.dedup.map((r) => ({
        key: r.rung,
        label: r.rung.replace(/_/g, " "),
        value: r.dropped ?? 0,
        sub: r.kept ? `${nf.format(r.kept)} near-miss${r.kept === 1 ? "" : "es"} kept` : undefined,
      }))}
      color={STATUS_INFO.duplicate.color}
      empty="No duplicates caught in this window."
    />
  );
}

// ── the tab ───────────────────────────────────────────────────────────────

export function StatsPanel({ channel }: { channel: string }) {
  const [range, setRange] = useState<StatsRange>("7d");

  // Remember the window per browser — a convenience, never required.
  useEffect(() => {
    try {
      const saved = localStorage.getItem(RANGE_KEY) as StatsRange | null;
      if (saved && RANGES.some((r) => r.id === saved)) setRange(saved);
    } catch {
      // Storage unavailable: the default window is fine.
    }
  }, []);

  function chooseRange(next: StatsRange) {
    setRange(next);
    try {
      localStorage.setItem(RANGE_KEY, next);
    } catch {
      // Non-fatal.
    }
  }

  const { data, isFetching, isPlaceholderData } = useStats(channel, range);

  if (data && !data.ready) {
    return (
      <div className="p-4">
        <EmptyState channel={channel} />
      </div>
    );
  }

  const summary = data?.summary;
  const rangeLong = RANGES.find((r) => r.id === range)?.long ?? "";

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-ink-primary">Channel overview</h2>
          <p className="text-xs text-ink-muted">
            {rangeLong} · last post {timeAgo(summary?.last_published_at ?? null)}
            {isFetching && " · refreshing…"}
          </p>
        </div>
        <div role="radiogroup" aria-label="Time window" className="inline-flex rounded-lg border border-border bg-surface-primary p-0.5">
          {RANGES.map((r) => (
            <button
              key={r.id}
              type="button"
              role="radio"
              aria-checked={range === r.id}
              onClick={() => chooseRange(r.id)}
              className={cn(
                "h-9 min-w-[3rem] rounded-md px-3 text-xs font-medium transition-colors sm:h-8",
                range === r.id ? "bg-ink-primary text-surface-primary" : "text-ink-muted hover:text-ink-primary"
              )}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      <div className={cn("flex flex-col gap-4 transition-opacity", isPlaceholderData && "opacity-60")}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Kpi
            label="Arrived"
            accent={ARRIVED}
            value={summary ? nf.format(summary.ingested) : "–"}
            sub={summary && <Delta now={summary.ingested} before={summary.previous?.ingested} />}
          />
          <Kpi
            label="Published"
            accent={PUBLISHED}
            value={summary ? nf.format(summary.published) : "–"}
            sub={summary && <Delta now={summary.published} before={summary.previous?.published} />}
          />
          <Kpi
            label="Publish rate"
            value={pct(summary?.publish_rate, 1)}
            sub={<span className="text-xs text-ink-muted">published ÷ arrived</span>}
          />
          <Kpi
            label="Time to publish"
            value={formatDuration(summary?.median_minutes_to_publish ?? null)}
            sub={<span className="text-xs text-ink-muted">median, arrival → sent</span>}
          />
          <Kpi
            label="In the pipeline"
            value={summary ? nf.format(summary.queued_now) : "–"}
            sub={<span className="text-xs text-ink-muted">{summary ? `${nf.format(summary.held_now)} held as fuel` : ""}</span>}
          />
          <Kpi
            label="Live stories"
            value={summary ? nf.format(summary.live_stories) : "–"}
            sub={<span className="text-xs text-ink-muted">being followed now</span>}
          />
        </div>

        <Section
          title="Activity"
          description={range === "24h" ? "Items arrived and posts sent, per hour" : "Items arrived and posts sent, per day"}
        >
          {data ? <ActivityChart data={data} range={range} /> : <Empty>Loading…</Empty>}
        </Section>

        <div className="grid gap-4 lg:grid-cols-2">
          <Section title="Where items end up" description="Every item that arrived in this window, by what the pipeline did with it">
            {data ? <OutcomeBreakdown data={data} /> : <Empty>Loading…</Empty>}
          </Section>
          <Section title="Market impact scores" description="How the sorter scored items 1–5, and how many of each went out">
            {data ? <ImportanceChart data={data} /> : <Empty>Loading…</Empty>}
          </Section>
        </div>

        <Section title="Sources" description="Which feeds earn their place: volume, how much of it posts, and how much is noise">
          {data ? <SourcesTable data={data} /> : <Empty>Loading…</Empty>}
        </Section>

        <div className="grid gap-4 lg:grid-cols-2">
          <Section title="Markets moved" description="Which market the sorter said has to reprice, by posts published">
            {data ? (
              <RankedList
                rows={data.markets.map((m) => ({
                  key: m.market,
                  label: m.market,
                  value: m.published ?? 0,
                  sub: `of ${nf.format(m.count)}`,
                }))}
                color={ARRIVED}
                empty="No markets tagged in this window."
              />
            ) : (
              <Empty>Loading…</Empty>
            )}
          </Section>
          <Section title="When posts go out" description="Posts sent by hour of day, in your local time">
            {data ? <HourOfDay data={data} /> : <Empty>Loading…</Empty>}
          </Section>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <Section title="Editor" description="The second AI's verdicts on written posts">
            {data ? <EditorView data={data} /> : <Empty>Loading…</Empty>}
          </Section>
          <Section title="Duplicate checks" description="Items dropped as repeats, by the check that caught them">
            {data ? <DedupView data={data} /> : <Empty>Loading…</Empty>}
          </Section>
        </div>
      </div>
    </div>
  );
}
