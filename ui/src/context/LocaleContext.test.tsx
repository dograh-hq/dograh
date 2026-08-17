import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { LocaleProvider, useLocale } from "./LocaleContext";

function LocaleProbe() {
  const { locale, setLocale, t } = useLocale();

  return (
    <>
      <span>{locale}</span>
      <span>{t("sidebar.telephony")}</span>
      <span>{t("telephony.reactivate")}</span>
      <span>{t("telephony.deleteConfiguration")}</span>
      <span>{t("telephony.copyConfigurationId")}</span>
      <span>{t("telephony.webhookKeyMissing")}</span>
      <span>{t("telephony.signatureSecretMissing")}</span>
      <button type="button" onClick={() => setLocale("en")}>
        Set English
      </button>
    </>
  );
}

describe("LocaleProvider", () => {
  afterEach(() => localStorage.clear());

  it("restores Portuguese and persists a language change", async () => {
    localStorage.setItem("dograh.locale", "pt-BR");
    render(
      <LocaleProvider>
        <LocaleProbe />
      </LocaleProvider>,
    );

    await waitFor(() => expect(screen.getByText("pt-BR")).toBeTruthy());
    expect(screen.getByText("Telefonia")).toBeTruthy();
    expect(screen.getByText("Reativar")).toBeTruthy();
    expect(screen.getByText("Excluir configuração")).toBeTruthy();
    expect(screen.getByText("Copiar ID da configuração")).toBeTruthy();
    expect(screen.getByText("Chave pública de webhook não configurada")).toBeTruthy();
    expect(screen.getByText("Segredo de assinatura não configurado")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Set English" }));

    expect(screen.getByText("en")).toBeTruthy();
    expect(localStorage.getItem("dograh.locale")).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });
});
