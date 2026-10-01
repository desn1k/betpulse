import { LegalPage, legalMetadata } from "@/components/legal/LegalPage";

export function generateMetadata() {
  return legalMetadata("consent");
}

export default function ConsentPage() {
  return <LegalPage id="consent" />;
}
