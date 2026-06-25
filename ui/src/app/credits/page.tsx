import { CreditsSection } from "@/components/CreditsSection";
import { IntegrationPage } from "@/components/IntegrationPage";

export default function CreditsPage() {
  return (
    <IntegrationPage
      eyebrow="Billing"
      title="Credits & Billing"
      subtitle="Your plan, remaining call credits, and top-ups."
      cardTitle="Credits & Billing"
      cardDescription="1 credit = 1 minute of calling. Top up anytime with Razorpay."
    >
      <CreditsSection />
    </IntegrationPage>
  );
}
