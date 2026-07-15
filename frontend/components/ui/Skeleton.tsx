import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * Loading placeholder. Callers give it the exact dimensions of the content it
 * stands in for, so swapping skeleton → content causes no layout shift (a hard
 * requirement for the match card: see `MatchCardSkeleton`).
 */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      className={cn("animate-pulse rounded-md bg-surface-muted", className)}
      {...props}
    />
  );
}
