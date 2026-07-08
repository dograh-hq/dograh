"use client";

import Cal from "@calcom/embed-react";
import { ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { useAppConfig } from "@/context/AppConfigContext";

import { CaptchaChallenge } from "./CaptchaChallenge";
import {
  EMPTY_ENTERPRISE_FIELDS,
  type EnterpriseFieldsValue,
  EnterpriseLeadFields,
} from "./EnterpriseLeadFields";
import { FormTrustLine } from "./FormTrustLine";
import { validateWorkEmail } from "./isPersonalEmail";
import { ENTERPRISE_DEPLOYMENT_SOURCES, type LeadSource } from "./leadFieldOptions";
import { LeadModalShell } from "./LeadModalShell";
import { submitLead } from "./submitLead";

interface EnterpriseModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source: LeadSource;
  // Optional values to pre-fill when the modal opens (e.g. company name already
  // collected upstream). Backward-compatible: omitted = no prefill.
  prefill?: { company?: string };
}

export function EnterpriseModal({ open, onOpenChange, source, prefill }: EnterpriseModalProps) {
  const { config } = useAppConfig();
  // Deployment provenance (analytics only); OSS submits via the public contact-sales path.
  const origin = config?.deploymentMode === "cloud" ? "cloud_app" : "oss_app";
  const [value, setValue] = useState<EnterpriseFieldsValue>(EMPTY_ENTERPRISE_FIELDS);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [captchaActive, setCaptchaActive] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // Cal.com booking link from the server's response (the server decides; the app only renders).
  const [calLink, setCalLink] = useState<string | null>(null);

  // The deployment question is only surfaced for custom-volume / Contact-Us /
  // pricing-custom-volume entry points; elsewhere it is hidden and the payload
  // defaults to "yes".
  const showDeployment = ENTERPRISE_DEPLOYMENT_SOURCES.includes(source);

  const reset = () => {
    setValue(EMPTY_ENTERPRISE_FIELDS);
    setEmailError(null);
    setCaptchaActive(false);
    setSubmitting(false);
    setCalLink(null);
  };

  const onFieldsChange = (patch: Partial<EnterpriseFieldsValue>) => {
    setValue((v) => ({ ...v, ...patch }));
    if ("workEmail" in patch) setEmailError(null);
  };

  // Seed company from prefill when the modal opens (don't clobber edits).
  const prefillCompany = prefill?.company;
  useEffect(() => {
    if (open && prefillCompany) {
      setValue((v) => (v.company ? v : { ...v, company: prefillCompany }));
    }
  }, [open, prefillCompany]);

  // Required fields, independent of the anti-spam check (revealed only after the
  // first submit click — see handleSubmit).
  const baseValid =
    Boolean(value.name.trim()) &&
    Boolean(value.company.trim()) &&
    Boolean(value.jobTitle.trim()) &&
    Boolean(value.workEmail.trim()) &&
    Boolean(value.phone.trim()) &&
    Boolean(value.volume);

  const canSubmit = baseValid && !submitting;

  // Validate, then pop the anti-spam check on top of the modal.
  const handleSubmit = () => {
    const err = validateWorkEmail(value.workEmail);
    if (err) { setEmailError(err); return; }
    if (!value.name.trim() || !value.company.trim() || !value.jobTitle.trim() || !value.phone.trim() || !value.volume) {
      toast.error("Please fill in all required fields");
      return;
    }
    setCaptchaActive(true);
  };

  // Runs once the captcha popup is verified.
  const doSubmit = async () => {
    setCaptchaActive(false);
    setSubmitting(true);
    try {
      const result = await submitLead({
        kind: "enterprise",
        source,
        origin,
        payload: {
          name: value.name,
          company: value.company,
          jobTitle: value.jobTitle,
          workEmail: value.workEmail,
          phone: value.phone,
          volume: value.volume,
          // Hidden entry points imply enterprise intent — default to "yes".
          deployment: showDeployment ? value.deployment || "yes" : "yes",
          agentGoal: value.agentGoal,
        },
      });
      // The server decides whether to return a booking link; if it does, show the calendar
      // inline, else the email note. The app only reads the response — no logic of its own.
      if (result?.show_calendar && result.cal_link) {
        setSubmitting(false);
        setCalLink(result.cal_link);
      } else {
        toast.success("Check your inbox - we just emailed you the next steps (give it a minute).");
        reset();
        onOpenChange(false);
      }
    } catch {
      toast.error("Something went wrong. Please try again.");
      setSubmitting(false);
    }
  };

  // Booking state: the server returned a booking link — show the inline calendar in the modal.
  if (calLink) {
    return (
      <LeadModalShell
        open={open}
        onOpenChange={(o) => { if (!o) reset(); onOpenChange(o); }}
        icon={ShieldCheck}
        eyebrow="Enterprise"
        title="Book a Strategy Call"
        description="Pick a time that works for you."
        primary={{ label: "Done", onClick: () => { reset(); onOpenChange(false); } }}
      >
        {/* Compact, zoomed-out calendar: render it larger, scale to 0.8, and clip the layout box left behind. */}
        <div className="overflow-hidden" style={{ height: "440px" }}>
          <Cal
            calLink={calLink}
            config={{ layout: "month_view", name: value.name, email: value.workEmail }}
            style={{ width: "113.64%", height: "500px", overflow: "auto", transform: "scale(0.88)", transformOrigin: "top left" }}
          />
        </div>
      </LeadModalShell>
    );
  }

  return (
    <LeadModalShell
      open={open}
      onOpenChange={(o) => { if (!o) reset(); onOpenChange(o); }}
      icon={ShieldCheck}
      eyebrow="Enterprise"
      title="Book a Strategy Call"
      description="SSO, on-prem, data residency, committed volume. Tell us about your environment."
      primary={{ label: "Submit", onClick: handleSubmit, disabled: !canSubmit, loading: submitting }}
      secondary={{ label: "Cancel", onClick: () => onOpenChange(false), disabled: submitting }}
      trustLine={<FormTrustLine />}
      overlay={captchaActive ? <CaptchaChallenge onVerified={doSubmit} onCancel={() => setCaptchaActive(false)} /> : undefined}
    >
      <EnterpriseLeadFields
        idPrefix="ent"
        value={value}
        onChange={onFieldsChange}
        showDeployment={showDeployment}
        emailError={emailError}
      />
    </LeadModalShell>
  );
}
