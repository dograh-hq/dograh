import { CreditsSection } from "@/components/CreditsSection";
import { IntegrationPage } from "@/components/IntegrationPage";

export default function CreditsPage() {
  return (
    <IntegrationPage
      eyebrow="Billing"
      title="Credits & Billing"
      subtitle="Track your plan, monitor remaining call credits, and top up in seconds."
      cardTitle="Credits & Billing"
      cardDescription="1 credit = 1 minute of calling. Top up anytime with secure payments via Razorpay."
    >
      <CreditsSection />
    </IntegrationPage>
  );
}
