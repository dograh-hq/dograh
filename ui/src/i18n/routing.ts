import { createNavigation } from "next-intl/navigation";
import { defineRouting } from "next-intl/routing";

export const routing = defineRouting({
  locales: ["it", "en"],
  defaultLocale: "it",
  localePrefix: "never",
});

export const { Link, redirect, usePathname, useRouter, getPathname } =
  createNavigation(routing);
