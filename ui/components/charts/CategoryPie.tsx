"use client";

import { ResponsiveContainer, PieChart, Pie, Cell, Tooltip, Legend } from "recharts";
import { CHART_COLORS } from "@/lib/constants";
import { ChartShell } from "./ChartShell";

interface Slice {
  name: string;
  value: number;
}

interface CategoryPieProps {
  data: Slice[];
}

const PIE_COLORS = [
  CHART_COLORS.critical,
  CHART_COLORS.high,
  CHART_COLORS.medium,
  CHART_COLORS.low,
  CHART_COLORS.accent,
  CHART_COLORS.info,
];

export function CategoryPie({ data }: CategoryPieProps) {
  return (
    <ChartShell title="Threat Categories" empty={data.length === 0}>
      <ResponsiveContainer width="100%" height={280}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={55}
            outerRadius={90}
            paddingAngle={2}
            dataKey="value"
            stroke="var(--ds-void)"
            strokeWidth={2}
          >
            {data.map((_, i) => (
              <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              background: "var(--ds-surface-2)",
              border: "1px solid var(--ds-border)",
              borderRadius: 8,
              fontSize: 12,
              fontFamily: "var(--ds-font-mono)",
            }}
          />
          <Legend
            iconType="circle"
            iconSize={8}
            wrapperStyle={{ fontSize: 11, fontFamily: "var(--ds-font-mono)" }}
          />
        </PieChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
