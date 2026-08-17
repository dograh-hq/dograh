interface ProviderBrandProps {
  logoUrl?: string | null;
  displayName?: string;
}

export function ProviderBrand({ logoUrl, displayName }: ProviderBrandProps) {
  if (!logoUrl) return null;

  return (
    <img
      src={logoUrl}
      alt={displayName ?? "Provider"}
      className="h-5 w-auto rounded-sm bg-zinc-950 px-1"
    />
  );
}
