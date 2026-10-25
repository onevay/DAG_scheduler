import { useId } from "react";

import type { MetricKey } from "./compare";

const R = 150;
// Лепесток от 34 до 100 по длине: даже худший виден, лучший доходит до внешнего круга
const petal = (score: number) => 34 + Math.round(Math.min(1, Math.max(0, score)) * 66);

const LABELS: Record<MetricKey, string> = {
  makespan: "makespan",
  util: "загрузка",
  wait: "ожидание",
  lost: "потери",
};

/** Профиль политики «цветком»: четыре лепестка — доли от лучших значений по метрикам. */
export function Flower({ policy, scores }: { policy: string; scores: Record<MetricKey, number> }) {
  const gradient = `petal${useId().replace(/:/g, "")}`;
  const pct = (k: MetricKey) => `${LABELS[k]} ${Math.round(scores[k] * 100)}%`;
  const half = (k: MetricKey) => petal(scores[k]) / 2 + 8;
  return (
    <svg
      role="img"
      aria-label={`Профиль ${policy}: ${(Object.keys(LABELS) as MetricKey[]).map(pct).join(", ")}`}
      viewBox="0 0 300 300"
      style={{ width: "100%", maxWidth: 320, alignSelf: "center" }}
    >
      <defs>
        <radialGradient id={gradient} cx="50%" cy="85%" r="90%">
          <stop offset="0%" stopColor="#f6b49a" />
          <stop offset="55%" stopColor="#f2de8a" />
          <stop offset="100%" stopColor="#b9e6a0" />
        </radialGradient>
      </defs>
      <circle cx={R} cy={R} r="118" fill="none" stroke="#eef1ee" strokeDasharray="3 6" />
      <circle cx={R} cy={R} r="70" fill="none" stroke="#eef1ee" strokeDasharray="3 6" />
      <g fill={`url(#${gradient})`} opacity="0.92" className="motion-data">
        <ellipse cx={R} cy={R - half("makespan")} rx="44" ry={half("makespan")} />
        <ellipse cx={R + half("util")} cy={R} rx={half("util")} ry="44" />
        <ellipse cx={R} cy={R + half("wait")} rx="44" ry={half("wait")} />
        <ellipse cx={R - half("lost")} cy={R} rx={half("lost")} ry="44" />
      </g>
      <circle cx={R} cy={R} r="22" fill="var(--color-ink)" />
      <path
        d="M143 150l5 5 10-10"
        fill="none"
        stroke="var(--color-accent)"
        strokeWidth="2.4"
        strokeLinecap="round"
      />
      <g
        fontFamily="Manrope, sans-serif"
        fontSize="13"
        fontWeight="600"
        fill="var(--color-ink)"
        aria-hidden="true"
      >
        <text x={R} y="20" textAnchor="middle">
          {pct("makespan")}
        </text>
        <text x="292" y="154" textAnchor="end">
          {pct("util")}
        </text>
        <text x={R} y="292" textAnchor="middle">
          {pct("wait")}
        </text>
        <text x="8" y="154">
          {pct("lost")}
        </text>
      </g>
    </svg>
  );
}
