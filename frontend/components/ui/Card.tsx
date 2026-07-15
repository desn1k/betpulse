import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/** Large, soft-shadowed surface — the primary content container (spec §0). */
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-card border border-border bg-surface shadow-card",
        className,
      )}
      {...props}
    />
  );
}
