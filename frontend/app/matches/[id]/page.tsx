import { MatchDetailView } from "@/components/match/MatchDetailView";

export default async function MatchPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <MatchDetailView id={id} />;
}
