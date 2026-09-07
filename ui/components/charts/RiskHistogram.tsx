"use client";

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
} from "recharts";
import { ChartShell } from "./ChartShell";

interface Bucket {
  range: string;
  count: number;
  color: string;
}

interface RiskHistogramProps {
  data: Bucket[];
}

export function RiskHistogram({ data }: RiskHistogramProps) {
  return (
    <ChartShell title="Risk Score Distribution" empty={data.length === 0}>
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--ds-border)" vertical={false} />
          <XAxis
            dataKey="range"
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
          <Bar dataKey="count" radius={[4, 4, 0, 0]}>
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
