"use client";

import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import { ChartShell } from "./ChartShell";

interface DataPoint {
  date: string;
  critical: number;
  high: number;
  medium: number;
  low: number;
}

interface SeverityTrendProps {
  data: DataPoint[];
}

export function SeverityTrend({ data }: SeverityTrendProps) {
  return (
    <ChartShell title="Severity Trend" empty={data.length === 0}>
      <ResponsiveContainer width="100%" height={260}>
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--ds-border)" />
          <XAxis
            dataKey="date"
            tick={{ fill: "var(--ds-text-muted)", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: "var(--ds-text-muted)", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            contentStyle={{
              background: "var(--ds-surface-2)",
              border: "1px solid var(--ds-border)",
              borderRadius: 8,
              fontSize: 12,
              fontFamily: "var(--ds-font-mono)",
            }}
          />
          <Area
            type="monotone"
            dataKey="critical"
            stackId="1"
            stroke="var(--ds-severity-critical)"
            fill="rgba(255, 45, 85, 0.2)"
          />
          <Area
            type="monotone"
            dataKey="high"
            stackId="1"
            stroke="var(--ds-severity-high)"
            fill="rgba(255, 149, 0, 0.2)"
          />
          <Area
            type="monotone"
            dataKey="medium"
            stackId="1"
            stroke="var(--ds-severity-medium)"
            fill="rgba(255, 204, 0, 0.15)"
          />
          <Area
            type="monotone"
            dataKey="low"
            stackId="1"
            stroke="var(--ds-severity-low)"
            fill="rgba(48, 209, 88, 0.15)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
