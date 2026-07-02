"use client";

import { useLocale } from "next-intl";
import { useRouter } from "@/i18n/routing";
import { useTransition } from "react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const FLAGS: Record<string, string> = {
  it: "🇮🇹",
  en: "🇬🇧",
};

const LABELS: Record<string, string> = {
  it: "Italiano",
  en: "English",
};

export function LanguageSwitcher() {
  const locale = useLocale();
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  function switchTo(nextLocale: string) {
    startTransition(() => {
      router.replace("/", { locale: nextLocale });
    });
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-1.5 text-sm">
          <span className="text-base">{FLAGS[locale]}</span>
          <span className="hidden sm:inline">{LABELS[locale]}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[120px]">
        {Object.entries(LABELS).map(([code, label]) => (
          <DropdownMenuItem
            key={code}
            onClick={() => switchTo(code)}
            disabled={code === locale || isPending}
            className="gap-2"
          >
            <span className="text-base">{FLAGS[code]}</span>
            <span>{label}</span>
            {code === locale && <span className="ml-auto text-xs text-muted-foreground">✓</span>}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
