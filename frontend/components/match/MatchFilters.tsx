"use client";

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import type { FixtureStatus } from "@/types/match";

// Launch coverage (spec §1). Phase 9's backtester extends the filter surface;
// the list keeps to league + status, wired through the same query params.
export const LEAGUES = ["EPL", "LALIGA", "SERIEA", "BUNDESLIGA", "LIGUE1", "UCL", "RPL"] as const;
const STATUSES: FixtureStatus[] = ["scheduled", "live"];

interface MatchFiltersProps {
  league: string | undefined;
  status: FixtureStatus | undefined;
  onLeagueChange: (league: string | undefined) => void;
  onStatusChange: (status: FixtureStatus | undefined) => void;
}

export function MatchFilters({
  league,
  status,
  onLeagueChange,
  onStatusChange,
}: MatchFiltersProps) {
  const t = useTranslations();

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <FilterChip active={status === undefined} onClick={() => onStatusChange(undefined)}>
          {t("filters.allStatuses")}
        </FilterChip>
        {STATUSES.map((s) => (
          <FilterChip key={s} active={status === s} onClick={() => onStatusChange(s)}>
            {t(`card.${s}`)}
          </FilterChip>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <FilterChip active={league === undefined} onClick={() => onLeagueChange(undefined)}>
          {t("filters.allLeagues")}
        </FilterChip>
        {LEAGUES.map((code) => (
          <FilterChip key={code} active={league === code} onClick={() => onLeagueChange(code)}>
            {code}
          </FilterChip>
        ))}
      </div>
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant={active ? "primary" : "secondary"}
      onClick={onClick}
      aria-pressed={active}
      className={cn(!active && "font-medium")}
    >
      {children}
    </Button>
  );
}
