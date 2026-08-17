# Dograh AI

> 💡 **Aviso:** esta documentação é mantida pela comunidade. Se encontrar uma tradução imprecisa ou conteúdo diferente da versão em inglês, abra um Pull Request.

**A alternativa open source e auto-hospedável ao Vapi e Retell**: crie agentes de voz para produção com um construtor visual de fluxos, teste em minutos e use assistentes de programação com IA via MCP para criar e editar fluxos.

<p align="center">
  <a href="https://app.dograh.com">
    <img src="https://img.shields.io/badge/▶_Experimente_a_nuvem-app.dograh.com-2563eb?style=for-the-badge" alt="Experimente a nuvem">
  </a>
  &nbsp;
  <a href="#-início-rápido">
    <img src="https://img.shields.io/badge/⚡_Auto--hospede_em_60s-Um_comando-111827?style=for-the-badge" alt="Auto-hospede em 60 segundos">
  </a>
  &nbsp;
  <a href="https://join.slack.com/t/dograh-community/shared_invite/zt-3zjb5vwvl-j7hRz3_F1SOn5cH~jm5f5g">
    <img src="https://img.shields.io/badge/💬_Entre_no_Slack-Comunidade-4A154B?style=for-the-badge&logo=slack" alt="Entre no Slack">
  </a>
</p>

<p align="center">
  <a href="https://docs.dograh.com">📖 Documentação</a> &nbsp;·&nbsp;
  <a href="LICENSE">📜 BSD 2-Clause</a> &nbsp;·&nbsp;
  <a href="README.md">🌐 English</a> &nbsp;·&nbsp;
  <a href="README.zh-CN.md">🌐 中文</a> &nbsp;·&nbsp;
  <a href="README.ja-JP.md">🌐 日本語</a>
</p>

<p align="center">
  <img src="docs/images/hero.gif" alt="Dograh em ação: crie um fluxo, inicie um agente de voz e converse" width="80%">
</p>

- **100% open source e auto-hospedável**: sem dependência de fornecedor.
- **Controle e transparência**: o código é aberto e as integrações de LLM, TTS e STT podem ser adaptadas.
- **Navegação e configuração de telefonia em inglês e português (Brasil)**: alterne o idioma pelo seletor no topo da aplicação.

## 🚀 Início rápido

### Instalação local

> **Nota:** o Dograh coleta dados de uso anônimos para melhorar o produto. Para desativar, defina `ENABLE_TELEMETRY=false` antes de executar o script de inicialização.

```bash
curl -o docker-compose.yaml https://raw.githubusercontent.com/dograh-hq/dograh/main/docker-compose.yaml && curl -o start_docker.sh https://raw.githubusercontent.com/dograh-hq/dograh/main/scripts/start_docker.sh && chmod +x start_docker.sh && ./start_docker.sh
```

Na primeira inicialização, o download das imagens pode levar de 2 a 3 minutos. Depois, abra `http://localhost:3010` para criar seu primeiro agente de voz.

Para instalar em um servidor remoto, consulte o [guia de implantação Docker](https://docs.dograh.com/deployment/docker#option-2:-remote-server-deployment).

### Configure com um agente de IA

Se você usa **Claude Code** ou **Codex**, instale a [skill oficial de instalação do Dograh](https://github.com/dograh-hq/dograh-plugins). Ela detecta o sistema operacional, escolhe o caminho de implantação e valida o resultado.

```text
# No Claude Code
/plugin marketplace add dograh-hq/dograh-plugins
/plugin install dograh@dograh
```

## 🎙️ Seu primeiro agente de voz

1. Abra `http://localhost:3010` no navegador.
2. Escolha **Inbound** ou **Outbound**, dê um nome ao agente e descreva o caso de uso.
3. Clique em **Test Agent**.
4. Use **Test Audio** para falar pelo navegador ou **Test Chat** para iterar rapidamente em texto.

> 🔑 Não são necessárias chaves de API para começar. Você pode conectar suas próprias chaves de LLM, TTS, STT e telefonia quando quiser.

## Recursos

### Construtor de agentes de voz

- Editor visual de fluxos com nós de início, agentes, instruções globais, ferramentas, transições e encerramento de chamadas.
- Painel **Test Agent** com **Test Audio** e **Test Chat**.
- Bases de conhecimento, webhooks, embeds e chamadas de ferramentas.

### Voz e telefonia

- Integrações com Twilio, Vonage, Telnyx, Plivo, Vobiz, Cloudonix, Asterisk ARI e PAPI VoIP.
- Transferência para atendente humano em provedores compatíveis.
- Use seus próprios provedores de LLM, TTS, STT e telefonia.

### Experiência para desenvolvedores

- Instalação Docker com um comando.
- Backend Python e arquitetura modular de provedores.
- SDKs Python e Node para criação programática de agentes e chamadas de saída.
- Interface em inglês e Português do Brasil.

## Implantação

### Desenvolvimento local

Consulte o [guia de configuração local](https://docs.dograh.com/contribution/setup).

### Auto-hospedagem

Consulte o [guia de implantação Docker](https://docs.dograh.com/deployment/docker#option-2-remote-server-deployment) para instalar em servidor com HTTPS.

### Versão em nuvem

Visite [dograh.com](https://www.dograh.com/) para a versão gerenciada.

## 📚 Documentação

Acesse [docs.dograh.com](https://docs.dograh.com/) para a documentação técnica.

## 🤝 Comunidade e suporte

- **Slack**: participe da comunidade global, discuta recursos e peça ajuda de implantação.
- **GitHub Discussions**: compartilhe casos de uso e receitas de fluxos.
- **GitHub Issues**: reporte bugs ou solicite recursos.
- **Suporte em Português (Brasil)**: fale com Rafa Martins pelo [WhatsApp](https://wa.me/5527999082624).

👉 Comunidade global: [Dograh Community Slack](https://join.slack.com/t/dograh-community/shared_invite/zt-3zjb5vwvl-j7hRz3_F1SOn5cH~jm5f5g)

## 🙌 Contribuindo

Contribuições são bem-vindas.

1. Faça um fork do repositório.
2. Crie uma branch de recurso: `git checkout -b feat/minha-alteracao`.
3. Faça o commit das alterações.
4. Envie a branch ao seu fork: `git push origin feat/minha-alteracao`.
5. Abra um Pull Request para a `main` do projeto.

## 📄 Licença

Dograh AI é licenciado sob a [BSD 2-Clause License](LICENSE).

## 🏢 Sobre

Desenvolvido com ❤️ pela **Dograh** (Zansat Technologies Private Limited), com o compromisso de manter a IA de voz aberta e acessível.
