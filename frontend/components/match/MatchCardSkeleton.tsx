import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils";

import { MATCH_CARD_HEIGHT } from "./MatchCard";

/**
 * Loading placeholder for a MatchCard. It reuses MATCH_CARD_HEIGHT and mirrors
 * the card's internal layout (header row, teams row, consensus bar) so the
 * transition from loading → loaded produces no layout shift.
 */
export function MatchCardSkeleton() {
  return (
    <Card
      className={cn(MATCH_CARD_HEIGHT, "flex flex-col justify-between p-4")}
      data-testid="match-card-skeleton"
    >
      <div className="flex items-center justify-between">
        <Skeleton className="h-4 w-12" />
        <Skeleton className="h-5 w-20" />
      </div>
      <div className="flex items-center justify-between gap-3">
        <Skeleton className="h-5 w-24" />
        <Skeleton className="h-5 w-24" />
      </div>
      <div className="flex flex-col gap-1">
        <Skeleton className="h-3 w-16" />
        <Skeleton className="h-7 w-full" />
      </div>
    </Card>
  );
}

/** A grid of skeleton cards for the initial list load. */
export function MatchListSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div
      className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
      aria-busy="true"
      aria-label="Loading matches"
    >
      {Array.from({ length: count }, (_, i) => (
        <MatchCardSkeleton key={i} />
      ))}
    </div>
  );
}
