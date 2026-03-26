# Arquitetura — Dashboard Koti

## Diagrama

```mermaid
flowchart TB
    subgraph Fontes["Fontes de Dados"]
        OMIE[Omie API]
        BP[BP.xlsx<br/>Google Drive]
        FOLHA[Folha 2026.xlsx<br/>Local]
    end

    subgraph Pipelines["Pipelines — GitHub Actions 3x/dia"]
        SYNC[omie_sync_bq.py]
        EBP[extract_bp_bq.py]
        ERH[extract_rh.py]
    end

    subgraph BQ["BigQuery — studio_koti"]
        LANC[lancamentos]
        SALDOS[saldos_bancarios]
        HIST[historico_saldos]
        CAT[categorias]
        PROJ[projetos]
        CLI[clientes]
        VENDAS[vendas_pedidos]
        SLOG[sync_log]
        ORC[orcamento_dre]
        FOLHA_BQ[folha_funcionarios]
    end

    subgraph Serving["Serving"]
        API[api_bq.py<br/>Cloud Function]
        BOT[bot_telegram.py<br/>Cloud Run — webhook]
    end

    subgraph Frontend["Frontend — GitHub Pages"]
        DASH[dashboard_bq.html<br/>7 abas + Visão Geral]
        RH[dashboard_rh.html<br/>decripta rh_data.enc]
    end

    subgraph AI["LLM"]
        HAIKU[Claude Haiku 4.5]
    end

    TELEGRAM[Telegram Bot API<br/>webhook]

    OMIE --> SYNC
    BP --> EBP
    FOLHA --> ERH

    SYNC --> LANC & SALDOS & HIST & CAT & PROJ & CLI & VENDAS & SLOG
    EBP --> ORC
    ERH --> FOLHA_BQ

    BQ --> API
    BQ --> BOT

    API --> DASH
    ERH -.->|rh_data.enc| RH
    ERH -.->|rh_data.enc| DASH

    BOT <--> HAIKU
    BOT <--> TELEGRAM
```

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Linguagem | Python 3.11 (pipelines), 3.12 (bot Docker) |
| Data Warehouse | BigQuery |
| CI/CD | GitHub Actions (sync 3x/dia + deploy bot on push) |
| Bot | Cloud Run — webhook mode, auto-discover URL |
| API | Cloud Functions (us-central1) |
| Frontend | HTML/JS — GitHub Pages, Chart.js |
| LLM | Claude Haiku 4.5 (Anthropic SDK) |
| Mensageria | Telegram Bot API (webhook via Cloud Run) |
| Criptografia | AES-256-GCM + PBKDF2 (rh_data), PBKDF2 (login) |
| Secrets | GCP Secret Manager (bot tokens), GitHub Secrets (SA key) |

## Fluxo de dados

1. **Ingestão**: GitHub Actions roda 3x/dia (5h, 12h, 18h BRT)
   - `omie_sync_bq.py` — puxa lançamentos, saldos, clientes, projetos, vendas do Omie (filtra contas fictícias via CONTAS_IGNORAR)
   - `extract_bp_bq.py` — extrai DRE Real vs Orçado da planilha BP.xlsx (valida DRE_MAP labels antes de processar)
   - `extract_rh.py` — extrai folha agregada → `rh_data.json` + `rh_data.enc` + upload BQ (filtra subtotais de departamento)

2. **Dashboard** (`dashboard_bq.html`): `api_bq.py` (Cloud Function) serve JSON → renderiza com Chart.js
   - **Visão Geral** (premium design): 6 KPIs hero, 3 sparklines alinhados (mesmos 6 meses), alertas automáticos, mini DRE YTD. KPI Resultado e sparkline Margem usam regime competência. Headcount carrega async do `rh_data.enc`
   - **Fluxo de Caixa**: KPIs + aging/inadimplência (auto-oculta se zero) + despesas/receitas
   - **Financeiro**: Contas a pagar/receber, saldos bancários
   - **Projetos**: KPIs + scatter margem (>70K, exclui SK/Studio Koti) + barras + tabela detalhada
   - **Vendas e Clientes**: vendas + análise de clientes consolidados na mesma aba
   - **Conciliação FD**, **Real vs Orçado**: regime competência
   - `dashboard_rh.html` carrega `rh_data.enc` e decripta no browser com crypto.subtle
   - Login com PBKDF2 (10k iterações, salt fixo, sem fallback plaintext)
   - Badges de regime contábil inline nos botões das tabs

3. **Bot Telegram**:
   - Usuário pergunta no Telegram → webhook no Cloud Run
   - `bot_telegram.py` envia para Claude Haiku → gera SQL BigQuery
   - Pós-processamento: injeta `nome` em queries de folha, remove GROUP BY departamento indevido
   - Executa no BigQuery → Haiku formata resposta → envia no Telegram
   - RBAC: chat IDs autorizados, exec IDs para folha/saldos
   - Exec: sempre mostra nomes individuais (nunca substitui por departamento)

## Segurança

- **Dados RH**: encriptados em repouso (`rh_data.enc`), `rh_data.json` no `.gitignore`
- **Login dashboard**: PBKDF2 sem fallback plaintext
- **Bot**: rate-limit, SQL read-only, tabelas restritas por nível de acesso
- **Secrets**: GCP Secret Manager para tokens, GitHub Secrets para SA key
- **Rotação**: trimestral (SA key, bot token, API key) — ver CLAUDE.md
- **Transferências**: verificado que não há categorias de transferência entre contas nos dados
