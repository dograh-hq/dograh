interface ProviderBrandProps {
  provider: string;
}

export function ProviderBrand({ provider }: ProviderBrandProps) {
  if (provider !== "papi_voip") return null;

  return (
    <img
      src="/providers/papi-logo.png"
      alt="PAPI"
      className="h-5 w-auto rounded-sm bg-zinc-950 px-1"
    />
  );
}
