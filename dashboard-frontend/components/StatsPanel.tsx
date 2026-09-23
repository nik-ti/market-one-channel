// Stats tab: three views built with Recharts, all driven by one /stats call.
"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useStats } from "@/hooks/useApi";

const GATE_COLORS: Record<string, string> = {
  published: "#10B981",
  rejected: "#DC2626",
  held: "#F59E0B",
  expired: "#9CA3AF",
};

export function StatsPanel() {
  const { data, isFetching } = useStats();

  const sourceData = data?.sources_count ?? [];
  const gateData = data
    ? Object.entries(data.gate_outcomes).map(([name, value]) => ({ name, value }))
    : [];
  const trendData = data?.trends ?? [];

  return (
    <div className="flex flex-col gap-4 p-4">
      {isFetching && <span className="text-xs text-ink-muted">Refreshing...</span>}

      <Card>
        <CardHeader>
          <CardTitle>Posts by source</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={sourceData} layout="vertical" margin={{ left: 24 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" horizontal={false} />
                <XAxis type="number" stroke="var(--ink-muted)" fontSize={12} />
                <YAxis
                  type="category"
                  dataKey="source_name"
                  width={110}
                  stroke="var(--ink-muted)"
                  fontSize={12}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--surface-primary)",
                    border: "1px solid var(--border-color)",
                    fontSize: 12,
                  }}
                />
                <Bar dataKey="count" fill="#2563EB" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Gate outcomes</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={gateData}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={60}
                  outerRadius={100}
                  paddingAngle={2}
                >
                  {gateData.map((entry) => (
                    <Cell key={entry.name} fill={GATE_COLORS[entry.name] ?? "#94A3B8"} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: "var(--surface-primary)",
                    border: "1px solid var(--border-color)",
                    fontSize: 12,
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-2 flex flex-wrap justify-center gap-4 text-xs text-ink-muted">
            {gateData.map((entry) => (
              <span key={entry.name} className="flex items-center gap-1.5">
                <span
                  className="h-2.5 w-2.5 rounded-full"
                  style={{ background: GATE_COLORS[entry.name] ?? "#94A3B8" }}
                />
                {entry.name}: {entry.value}
              </span>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Posts per hour (last 24h)</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={trendData}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" />
                <XAxis
                  dataKey="hour"
                  stroke="var(--ink-muted)"
                  fontSize={11}
                  tickFormatter={(v: string) => v.slice(11, 16)}
                />
                <YAxis allowDecimals={false} stroke="var(--ink-muted)" fontSize={12} />
                <Tooltip
                  contentStyle={{
                    background: "var(--surface-primary)",
                    border: "1px solid var(--border-color)",
                    fontSize: 12,
                  }}
                />
                <Line type="monotone" dataKey="count" stroke="#2563EB" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
