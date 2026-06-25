import { IntegrationPage } from "@/components/IntegrationPage";
import { WhatsAppSection } from "@/components/WhatsAppSection";

export default function WhatsAppIntegrationPage() {
  return (
    <IntegrationPage
      eyebrow="Integration"
      title="WhatsApp Follow-up"
      subtitle="Send an approved WhatsApp template to the lead automatically after each call."
      cardTitle="WhatsApp Follow-up"
      cardDescription="Automatically send an approved WhatsApp template (with an optional document) to the lead after each call. Connect your own provider account and API key."
    >
      <WhatsAppSection />
    </IntegrationPage>
  );
}
