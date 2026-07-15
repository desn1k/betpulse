import { cn } from "@/lib/utils";
import { pct } from "@/lib/format";
import type { Probs1x2 } from "@/types/match";

interface ProbabilityBarProps {
  probs: Probs1x2;
  /** Accessible label prefix, e.g. the method name. */
  label: string;
  className?: string;
}

const SEGMENTS = [
  { key: "home", color: "bg-home" },
  { key: "draw", color: "bg-draw" },
  { key: "away", color: "bg-away" },
] as const;

/**
 * A stacked horizontal 1X2 bar: home / draw / away segments sized by probability.
 * Pure CSS (no chart lib) so it renders identically on server and client and is
 * trivially testable. The three values are assumed to sum to ~1.
 */
export function ProbabilityBar({ probs, label, className }: ProbabilityBarProps) {
  return (
    <div
      className={cn("flex h-7 w-full overflow-hidden rounded-md", className)}
      role="img"
      aria-label={`${label}: home ${pct(probs.home)}, draw ${pct(probs.draw)}, away ${pct(
        probs.away,
      )}`}
    >
      {SEGMENTS.map(({ key, color }) => {
        const value = probs[key];
        return (
          <div
            key={key}
            className={cn(
              "flex items-center justify-center text-[11px] font-semibold text-white",
              color,
            )}
            style={{ width: `${value * 100}%` }}
          >
            {value >= 0.12 ? pct(value) : ""}
          </div>
        );
      })}
    </div>
  );
}
