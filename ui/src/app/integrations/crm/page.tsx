import { CrmSection } from "@/components/CrmSection";
import { IntegrationPage } from "@/components/IntegrationPage";

export default function CrmIntegrationPage() {
  return (
    <IntegrationPage
      eyebrow="Integration"
      title="Connect your CRM"
      subtitle="Push every call to your CRM — contact, outcome, recording, transcript and sentiment."
      cardTitle="Connect your CRM"
      cardDescription="Automatically push every call to your CRM — upsert the contact and log the outcome, recording, transcript and sentiment. Connect your own CRM account and API token."
    >
      <CrmSection />
    </IntegrationPage>
  );
}
