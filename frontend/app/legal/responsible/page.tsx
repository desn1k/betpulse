import { LegalPage, legalMetadata } from "@/components/legal/LegalPage";

export function generateMetadata() {
  return legalMetadata("responsible");
}

export default function ResponsiblePage() {
  return <LegalPage id="responsible" />;
}
