# Sativoice Enterprise

**Piattaforma Voice AI open-source per il mercato enterprise italiano** — basata su [Dograh](https://github.com/TopCS/dograh), l'alternativa self-hostabile a Vapi e Retell.

Sativoice Enterprise è la distribuzione italiana ufficiale mantenuta da **Satisfactory Group**.

- **100% open source** (BSD 2-Clause) — nessun vendor lock-in
- **Self-hosting on-premise** — i tuoi dati restano nella tua infrastruttura
- **GDPR-ready** — deployment su suolo italiano
- **Bring your own LLM / STT / TTS** — qualsiasi provider, o usa lo stack integrato

---

## ⚖️ Sativoice vs Vapi vs Retell

|  | **Sativoice** (basato su Dograh) | **Vapi** | **Retell** |
|---|---|---|---|
| **License** | BSD 2-Clause (open source) | Proprietary | Proprietary |
| **Self-hostable** | ✅ Yes — un comando Docker | ❌ SaaS only | ❌ SaaS only |
| **Pricing** | Free (self-host) | Per-minute SaaS | Per-minute SaaS |
| **Bring your own LLM / STT / TTS** | ✅ Qualsiasi provider | Configurabile | Configurabile |
| **Source-level customization** | ✅ Ogni linea è tua | ❌ Closed source | ❌ Closed source |
| **Data residency** | La tua infrastruttura | Their cloud | Their cloud |
| **Vendor lock-in** | Nessuno | Full | Full |

---

## 🚀 Deploy Locale

```bash
curl -o docker-compose.yaml https://raw.githubusercontent.com/TopCS/sativoice/main/docker-compose.yaml && curl -o start_docker.sh https://raw.githubusercontent.com/TopCS/sativoice/main/scripts/start_docker.sh && chmod +x start_docker.sh && ./start_docker.sh
```

Primo avvio: 2-3 minuti per scaricare le immagini. Poi apri [http://localhost:3010](http://localhost:3010).

> **Nota:** Il progetto raccoglie dati di utilizzo anonimi. Disattiva con `ENABLE_TELEMETRY=false` prima di avviare.

---

## 🎙️ Primo Voice Bot

1. Apri [http://localhost:3010](http://localhost:3010)
2. Scegli **Inbound** o **Outbound**, dai un nome al bot e descrivi l'uso in 5-10 parole
3. Clicca **Web Call** — stai parlando col tuo bot

> 🔑 **Nessuna API key necessaria.** Sativoice include chiavi auto-generate e il proprio stack LLM/TTS/STT. Connetti le tue chiavi per LLM, TTS, STT o telefonia (Twilio, Vonage, Telnyx, etc.) in qualsiasi momento.

---

## Features

### Voice

- **Telefonia:** Twilio, Vonage, Vobiz, Cloudonix, Telnyx, Plivo, ARI — aggiungine altri
- **Lingue:** Supporto multilingua
- **Modelli custom:** Porta i tuoi TTS/STT
- **Real-time:** Interazioni vocali a bassa latenza
- **Transfer call:** Passa chiamate a operatori umani

### Developer Experience

- **Zero config:** Chiavi API auto-generate per test immediati
- **Python:** Backend in FastAPI, personalizzabile
- **Docker-first:** Containerizzato per deploy consistenti
- **Architettura modulare:** Sostituisci componenti al volo

### Testing

- **Test Mode:** Prova l'agente end-to-end prima di pubblicare
- **Web Calls:** Parla col bot direttamente dal builder — nessuna configurazione telefonica
- **QA Node:** Nodo di workflow per analizzare la qualità dei prompt

---

## Deploy

- **Locale:** `./start_docker.sh` (questo README)
- **Self-Hosted:** [Guida Docker Deployment](https://docs.sativoice.com/deployment/docker)
- **Contributor:** [Guida sviluppo locale](https://docs.sativoice.com/contribution/setup)

---

## 📦 SDK

- **Python SDK** — `dograh_sdk` (nel repo)
- **TypeScript SDK** — `@dograh/sdk` (nel repo)

---

## 🙌 Upstream

Sativoice Enterprise è un fork di **[Dograh](https://github.com/TopCS/dograh)** — la piattaforma voice AI open-source mantenuta da YC alumni. Il codice interno (classi, SDK, env var) mantiene i riferimenti a Dograh per garantire piena compatibilità con l'upstream.

---

## 📄 Licenza

BSD 2-Clause — vedi [LICENSE](LICENSE).

---

## 🏢 Sativoice Enterprise

Mantenuto da **Satisfactory Group** per il mercato enterprise italiano.
Per informazioni: enterprise@sativoice.com
