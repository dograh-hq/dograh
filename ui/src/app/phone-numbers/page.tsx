import { IntegrationPage } from "@/components/IntegrationPage";
import { PhoneNumbersSection } from "@/components/PhoneNumbersSection";

export default function PhoneNumbersPage() {
  return (
    <IntegrationPage
      eyebrow="Telephony"
      title="Phone Numbers"
      subtitle="Buy and manage outbound numbers for your campaigns."
      cardTitle="Phone Numbers"
      cardDescription="Buy a phone number for outbound calls. Requires completed KYC; charged to your call-credit balance."
    >
      <PhoneNumbersSection />
    </IntegrationPage>
  );
}
