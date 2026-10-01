import { LegalPage, legalMetadata } from "@/components/legal/LegalPage";

export function generateMetadata() {
  return legalMetadata("disclaimer");
}

export default function DisclaimerPage() {
  return <LegalPage id="disclaimer" />;
}
