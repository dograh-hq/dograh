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
  },
  "pt-BR": {
    "header.openMenu": "Abrir menu",
    "header.joinSlack": "Entrar no Slack",
    "header.language": "Idioma",
    "header.retry": "Tentar novamente",
    "header.backendFailed": "Falha na conexao com o backend",
    "sidebar.overview": "Visao geral",
    "sidebar.build": "CRIAR",
    "sidebar.voiceAgents": "Agentes de voz",
    "sidebar.campaigns": "Campanhas",
    "sidebar.models": "Modelos",
    "sidebar.telephony": "Telefonia",
    "sidebar.tools": "Ferramentas",
    "sidebar.files": "Arquivos",
    "sidebar.recordings": "Gravacoes",
    "sidebar.developers": "Desenvolvedores",
    "sidebar.manage": "GERENCIAR",
    "sidebar.agentRuns": "Execucoes de agentes",
    "sidebar.billing": "Faturamento",
    "sidebar.reports": "Relatorios",
    "telephony.title": "Configuracoes de telefonia",
    "telephony.description": "Conecte uma ou mais contas de provedores de telefonia. Cada campanha usa uma configuracao; chamadas recebidas sao encaminhadas pela conta correta.",
    "telephony.learnMore": "Saiba mais",
    "telephony.addConfiguration": "Adicionar configuracao",
    "telephony.default": "Padrao",
    "telephony.inactive": "Inativa",
    "telephony.managePhoneNumbers": "Gerenciar numeros",
    "telephony.noConfigurations": "Nenhuma configuracao de telefonia",
    "telephony.noConfigurationsDescription": "Adicione uma configuracao para habilitar chamadas de saida e receber chamadas.",
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
    const savedLocale = localStorage.getItem(STORAGE_KEY);
    if (savedLocale === "en" || savedLocale === "pt-BR") {
      setLocaleState(savedLocale);
      document.documentElement.lang = savedLocale;
    }
  }, []);

  const setLocale = (nextLocale: Locale) => {
    setLocaleState(nextLocale);
    localStorage.setItem(STORAGE_KEY, nextLocale);
    document.documentElement.lang = nextLocale;
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
