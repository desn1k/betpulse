import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";

/** Detail-page placeholder mirroring the header → consensus → method bars layout. */
export function MatchDetailSkeleton() {
  return (
    <div className="flex flex-col gap-6" aria-busy="true">
      <Skeleton className="h-4 w-24" />
      <Card className="flex flex-col gap-6 p-6">
        <div className="flex flex-col gap-3">
          <Skeleton className="h-3 w-32" />
          <div className="flex items-center justify-between">
            <Skeleton className="h-6 w-32" />
            <Skeleton className="h-6 w-16" />
            <Skeleton className="h-6 w-32" />
          </div>
        </div>
        <Skeleton className="h-9 w-full" />
        <div className="flex flex-col gap-3">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="flex flex-col gap-1">
              <Skeleton className="h-4 w-28" />
              <Skeleton className="h-7 w-full" />
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
