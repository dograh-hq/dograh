"use client";

import { Languages } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useLocale, type Locale } from "@/context/LocaleContext";

const locales: Record<Locale, { code: string; flag: string; label: string }> = {
  en: { code: "EN", flag: "🇺🇸", label: "English" },
  "pt-BR": { code: "PT-BR", flag: "🇧🇷", label: "Português (Brasil)" },
};

export function LocaleSelector() {
  const { locale, setLocale, t } = useLocale();
  const selected = locales[locale];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          aria-label={`${t("header.language")}: ${selected.code}`}
          className="gap-1.5 px-2"
        >
          <Languages className="h-4 w-4" />
          <span aria-hidden>{selected.flag}</span>
          <span className="hidden sm:inline">{selected.code}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup
          value={locale}
          onValueChange={(value) => setLocale(value as Locale)}
        >
          {(Object.entries(locales) as Array<[Locale, (typeof locales)[Locale]]>).map(
            ([value, option]) => (
              <DropdownMenuRadioItem key={value} value={value}>
                <span aria-hidden>{option.flag}</span>
                {option.label}
              </DropdownMenuRadioItem>
            ),
          )}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
