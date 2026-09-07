"use client";

import {
  ResponsiveContainer,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  Tooltip,
} from "recharts";
import { ChartShell } from "./ChartShell";

interface RadarPoint {
  axis: string;
  value: number;
}

interface ConfidenceRadarProps {
  data: RadarPoint[];
}

export function ConfidenceRadar({ data }: ConfidenceRadarProps) {
  return (
    <ChartShell title="Confidence Radar" empty={data.length === 0}>
      <ResponsiveContainer width="100%" height={280}>
        <RadarChart cx="50%" cy="50%" outerRadius="70%" data={data}>
          <PolarGrid stroke="var(--ds-border)" />
          <PolarAngleAxis
            dataKey="axis"
            tick={{ fill: "var(--ds-text-muted)", fontSize: 11 }}
          />
          <PolarRadiusAxis
            angle={30}
            domain={[0, 1]}
            tick={{ fill: "var(--ds-text-muted)", fontSize: 10 }}
          />
          <Radar
            dataKey="value"
            stroke="var(--ds-accent)"
            fill="rgba(0, 229, 160, 0.2)"
            fillOpacity={0.6}
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
        </RadarChart>
      </ResponsiveContainer>
    </ChartShell>
  );
}
