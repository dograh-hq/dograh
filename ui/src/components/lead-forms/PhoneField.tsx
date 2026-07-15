"use client";

// Dark-themed wrapper around react-international-phone's PhoneInput.
// Emits a clean E.164 string (the backend geo/qualification rule keys off the
// dial code). The library is styled with its own CSS variables, which we map to
// our dark surface tokens so the field matches the rest of the form. Default
// country is the US; the user can switch via the flag selector.

import "react-international-phone/style.css";

import { PhoneInput } from "react-international-phone";

import { cn } from "@/lib/utils";

interface PhoneFieldProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
  disabled?: boolean;
}

// Map the library's theming variables onto our dark surface tokens so the
// control reads as one cohesive input rather than a third-party widget.
const phoneThemeVars: React.CSSProperties = {
  ["--react-international-phone-height" as string]: "2.25rem",
  ["--react-international-phone-background-color" as string]: "transparent",
  ["--react-international-phone-text-color" as string]: "var(--foreground)",
  ["--react-international-phone-border-color" as string]: "var(--input)",
  ["--react-international-phone-border-radius" as string]: "var(--radius-md)",
  ["--react-international-phone-font-size" as string]: "0.875rem",
  ["--react-international-phone-country-selector-background-color" as string]:
    "transparent",
  ["--react-international-phone-country-selector-background-color-hover" as string]:
    "var(--accent)",
  ["--react-international-phone-dropdown-item-background-color" as string]:
    "var(--popover)",
  ["--react-international-phone-dropdown-item-text-color" as string]:
    "var(--popover-foreground)",
  ["--react-international-phone-dropdown-item-background-color-hover" as string]:
    "var(--accent)",
  ["--react-international-phone-selected-dropdown-item-background-color" as string]:
    "var(--accent)",
};

export function PhoneField({ id, value, onChange, required, disabled }: PhoneFieldProps) {
  return (
    <div style={phoneThemeVars} className="phone-field-dark">
      <PhoneInput
        defaultCountry="us"
        value={value}
        onChange={(phone) => onChange(phone)}
        disabled={disabled}
        inputProps={{ id, required }}
        className="w-full"
        inputClassName={cn(
          "!w-full !bg-transparent !text-foreground placeholder:!text-muted-foreground",
          "focus-visible:!border-ring focus-visible:!ring-[3px] focus-visible:!ring-ring/50 !outline-none",
        )}
        countrySelectorStyleProps={{
          buttonClassName: "!h-9 !border-input !bg-transparent",
        }}
      />
    </div>
  );
}
