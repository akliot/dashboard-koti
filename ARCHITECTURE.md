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
        BOT[bot_telegram.py<br/>Cloud Run]
    end

    subgraph Frontend["Frontend — GitHub Pages"]
        DASH[dashboard_bq.html]
        RH[dashboard_rh.html]
    end

    subgraph AI["LLM"]
        HAIKU[Claude Haiku 4.5]
    end

    TELEGRAM[Telegram Bot API]

    OMIE --> SYNC
    BP --> EBP
    FOLHA --> ERH

    SYNC --> LANC & SALDOS & HIST & CAT & PROJ & CLI & VENDAS & SLOG
    EBP --> ORC
    ERH --> FOLHA_BQ

    BQ --> API
    BQ --> BOT

    API --> DASH
    API --> RH

    BOT <--> HAIKU
    BOT <--> TELEGRAM
```

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Linguagem | Python 3.11 |
| Data Warehouse | BigQuery |
| CI/CD | GitHub Actions |
| Bot | Cloud Run (webhook) |
| API | Cloud Functions |
| Frontend | HTML/JS — GitHub Pages |
| LLM | Claude Haiku 4.5 (Anthropic) |
| Mensageria | Telegram Bot API |

## Fluxo de dados

1. **Ingestão**: GitHub Actions roda 3x/dia (5h, 12h, 18h BRT) — puxa dados do Omie, BP e Folha para BigQuery
2. **Dashboard**: `api_bq.py` (Cloud Function) serve JSON agregado → `dashboard_bq.html` renderiza gráficos
3. **Bot**: usuário pergunta no Telegram → `bot_telegram.py` (Cloud Run) envia para Claude Haiku → Haiku gera SQL → executa no BigQuery → Haiku formata resposta → envia no Telegram
