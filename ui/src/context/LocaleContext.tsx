"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Locale = "en" | "pt-BR";

const STORAGE_KEY = "dograh.locale";

const translations = {
  en: {
    "header.openMenu": "Open menu",
    "header.joinSlack": "Join Slack",
    "header.language": "Language",
    "header.retry": "Retry",
    "header.backendFailed": "Backend connection failed",
    "sidebar.overview": "Overview",
    "sidebar.build": "BUILD",
    "sidebar.voiceAgents": "Voice Agents",
    "sidebar.campaigns": "Campaigns",
    "sidebar.models": "Models",
    "sidebar.telephony": "Telephony",
    "sidebar.tools": "Tools",
    "sidebar.files": "Files",
    "sidebar.recordings": "Recordings",
    "sidebar.developers": "Developers",
    "sidebar.manage": "MANAGE",
    "sidebar.agentRuns": "Agent Runs",
    "sidebar.billing": "Billing",
    "sidebar.reports": "Reports",
    "telephony.title": "Telephony configurations",
    "telephony.description": "Connect one or more telephony provider accounts. Each campaign uses one configuration; inbound calls are routed to the right one by account ID.",
    "telephony.learnMore": "Learn more",
    "telephony.addConfiguration": "Add configuration",
    "telephony.default": "Default",
    "telephony.inactive": "Inactive",
    "telephony.managePhoneNumbers": "Manage Phone Numbers",
    "telephony.noConfigurations": "No telephony configurations yet",
    "telephony.noConfigurationsDescription": "Add one to enable outbound calls and receive inbound calls.",
    "telephony.failedToLoadConfigurations": "Failed to load configurations",
    "telephony.failedToLoadConfiguration": "Failed to load configuration",
    "telephony.defaultOutboundSuccess": "is now the default outbound configuration",
    "telephony.failedToSetDefault": "Failed to set default",
    "telephony.reactivatedSuccess": "reactivated - reconnecting within a minute",
    "telephony.failedToReactivate": "Failed to reactivate configuration",
    "telephony.configurationDeleted": "Configuration deleted",
    "telephony.failedToDelete": "Failed to delete configuration",
    "telephony.phoneNumber": "phone number",
    "telephony.phoneNumbers": "phone numbers",
    "telephony.disabledAfterFailures": "Disabled after repeated connection failures",
    "telephony.copyConfigurationId": "Copy configuration ID",
    "telephony.configurationId": "Configuration ID",
    "telephony.configurationIdCopied": "Configuration ID copied",
    "telephony.failedToCopyId": "Failed to copy ID",
    "telephony.reconnectNow": "Reconnect this configuration now",
    "telephony.reactivate": "Reactivate",
    "telephony.setDefaultOutbound": "Set as default outbound",
    "telephony.edit": "Edit",
    "telephony.delete": "Delete",
    "telephony.cancel": "Cancel",
    "telephony.managePhoneNumbersFor": "Manage phone numbers for",
    "telephony.deleteConfiguration": "Delete configuration",
    "telephony.deleteConfigurationDescription": "and all of its phone numbers will be removed. Any campaigns that reference this configuration will block the deletion until they are reassigned.",
    "telephony.webhookKeyMissing": "Webhook public key not configured",
    "telephony.webhookKeyMissingSingular": "Telnyx configuration is missing a webhook public key. Without it, call status updates and inbound calls are being rejected. Copy your public key from Mission Control Portal > Keys & Credentials > Public Key and paste it into the affected configuration below.",
    "telephony.webhookKeyMissingPlural": "Telnyx configurations are missing a webhook public key. Without it, call status updates and inbound calls are being rejected. Copy your public key from Mission Control Portal > Keys & Credentials > Public Key and paste it into the affected configuration below.",
    "telephony.signatureSecretMissing": "Signature secret not configured",
    "telephony.signatureSecretMissingSingular": "Vonage configuration is missing a signature secret. Without it, signed webhooks are rejected, so inbound calls and call status updates will not work. Copy the signature secret from your provider account and paste it into the affected configuration below.",
    "telephony.signatureSecretMissingPlural": "Vonage configurations are missing a signature secret. Without it, signed webhooks are rejected, so inbound calls and call status updates will not work. Copy the signature secret from your provider account and paste it into the affected configuration below.",
  },
  "pt-BR": {
    "header.openMenu": "Abrir menu",
    "header.joinSlack": "Entrar no Slack",
    "header.language": "Idioma",
    "header.retry": "Tentar novamente",
    "header.backendFailed": "Falha na conexão com o backend",
    "sidebar.overview": "Visão geral",
    "sidebar.build": "CRIAR",
    "sidebar.voiceAgents": "Agentes de voz",
    "sidebar.campaigns": "Campanhas",
    "sidebar.models": "Modelos",
    "sidebar.telephony": "Telefonia",
    "sidebar.tools": "Ferramentas",
    "sidebar.files": "Arquivos",
    "sidebar.recordings": "Gravações",
    "sidebar.developers": "Desenvolvedores",
    "sidebar.manage": "GERENCIAR",
    "sidebar.agentRuns": "Execuções de agentes",
    "sidebar.billing": "Faturamento",
    "sidebar.reports": "Relatórios",
    "telephony.title": "Configurações de telefonia",
    "telephony.description": "Conecte uma ou mais contas de provedores de telefonia. Cada campanha usa uma configuração; chamadas recebidas são encaminhadas pela conta correta.",
    "telephony.learnMore": "Saiba mais",
    "telephony.addConfiguration": "Adicionar configuração",
    "telephony.default": "Padrão",
    "telephony.inactive": "Inativa",
    "telephony.managePhoneNumbers": "Gerenciar números",
    "telephony.noConfigurations": "Nenhuma configuração de telefonia",
    "telephony.noConfigurationsDescription": "Adicione uma configuração para habilitar chamadas de saída e receber chamadas.",
    "telephony.failedToLoadConfigurations": "Falha ao carregar as configurações",
    "telephony.failedToLoadConfiguration": "Falha ao carregar a configuração",
    "telephony.defaultOutboundSuccess": "agora é a configuração padrão para chamadas de saída",
    "telephony.failedToSetDefault": "Falha ao definir como padrão",
    "telephony.reactivatedSuccess": "reativada - reconectando em até um minuto",
    "telephony.failedToReactivate": "Falha ao reativar a configuração",
    "telephony.configurationDeleted": "Configuração excluída",
    "telephony.failedToDelete": "Falha ao excluir a configuração",
    "telephony.phoneNumber": "número de telefone",
    "telephony.phoneNumbers": "números de telefone",
    "telephony.disabledAfterFailures": "Desativada após repetidas falhas de conexão",
    "telephony.copyConfigurationId": "Copiar ID da configuração",
    "telephony.configurationId": "ID da configuração",
    "telephony.configurationIdCopied": "ID da configuração copiado",
    "telephony.failedToCopyId": "Falha ao copiar o ID",
    "telephony.reconnectNow": "Reconectar esta configuração agora",
    "telephony.reactivate": "Reativar",
    "telephony.setDefaultOutbound": "Definir como padrão para chamadas de saída",
    "telephony.edit": "Editar",
    "telephony.delete": "Excluir",
    "telephony.cancel": "Cancelar",
    "telephony.managePhoneNumbersFor": "Gerenciar números de telefone de",
    "telephony.deleteConfiguration": "Excluir configuração",
    "telephony.deleteConfigurationDescription": "e todos os seus números de telefone serão removidos. As campanhas que usam esta configuração impedirão a exclusão até serem reatribuídas.",
    "telephony.webhookKeyMissing": "Chave pública de webhook não configurada",
    "telephony.webhookKeyMissingSingular": "configuração da Telnyx não possui uma chave pública de webhook. Sem ela, as atualizações de status e chamadas recebidas são rejeitadas. Copie a chave pública em Mission Control Portal > Keys & Credentials > Public Key e cole na configuração afetada abaixo.",
    "telephony.webhookKeyMissingPlural": "configurações da Telnyx não possuem uma chave pública de webhook. Sem ela, as atualizações de status e chamadas recebidas são rejeitadas. Copie a chave pública em Mission Control Portal > Keys & Credentials > Public Key e cole na configuração afetada abaixo.",
    "telephony.signatureSecretMissing": "Segredo de assinatura não configurado",
    "telephony.signatureSecretMissingSingular": "configuração da Vonage não possui um segredo de assinatura. Sem ele, webhooks assinados são rejeitados e chamadas recebidas e atualizações de status não funcionarão. Copie o segredo de assinatura na conta do provedor e cole na configuração afetada abaixo.",
    "telephony.signatureSecretMissingPlural": "configurações da Vonage não possuem um segredo de assinatura. Sem ele, webhooks assinados são rejeitados e chamadas recebidas e atualizações de status não funcionarão. Copie o segredo de assinatura na conta do provedor e cole na configuração afetada abaixo.",
  },
} as const;

export type TranslationKey = keyof typeof translations.en;

type LocaleContextValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: TranslationKey) => string;
};

const LocaleContext = createContext<LocaleContextValue | null>(null);

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("en");

  useEffect(() => {
    try {
      const savedLocale = localStorage.getItem(STORAGE_KEY);
      if (savedLocale === "en" || savedLocale === "pt-BR") {
        setLocaleState(savedLocale);
        if (typeof document !== "undefined") {
          document.documentElement.lang = savedLocale;
        }
      }
    } catch {
      // Ignore localStorage access failures (e.g. storage disabled / sandbox)
    }
  }, []);

  const setLocale = (nextLocale: Locale) => {
    setLocaleState(nextLocale);
    try {
      localStorage.setItem(STORAGE_KEY, nextLocale);
    } catch {
      // Ignore localStorage access failures
    }
    if (typeof document !== "undefined") {
      document.documentElement.lang = nextLocale;
    }
  };

  const t = (key: TranslationKey) => translations[locale][key] ?? translations.en[key];

  return (
    <LocaleContext.Provider value={{ locale, setLocale, t }}>
      {children}
    </LocaleContext.Provider>
  );
}

export function useLocale() {
  const context = useContext(LocaleContext);
  if (!context) throw new Error("useLocale must be used within LocaleProvider");
  return context;
}
