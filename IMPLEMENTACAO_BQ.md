# Dashboard Financeiro — Omie + BigQuery

**Projeto GCP**: `dashboard-koti-omie`
**Dashboard**: https://akliot.github.io/dashboard-koti/
**API**: https://us-central1-dashboard-koti-omie.cloudfunctions.net/api_dashboard
**Bot Telegram**: @Kotifin_bot
**Repositório**: https://github.com/akliot/dashboard-koti

> Sistema para empresas que usam Omie ERP. Primeiro cliente: **Studio Koti** (dataset `studio_koti`).

---

## 1. Arquitetura

```
┌──────────┐     ┌─────────────────┐     ┌──────────────┐     ┌────────────────┐
│ API Omie │────▶│ omie_sync_bq.py │────▶│   BigQuery    │────▶│ dashboard_bq   │
│ (ERP)    │     │ GitHub Actions   │     │   (GCP)      │     │ .html (GitHub  │
└──────────┘     │ 3x/dia MERGE    │     └──────┬───────┘     │  Pages)        │
                 └─────────────────┘            │             └────────────────┘
                                                │                    ▲
                  ┌─────────────────┐           │                    │ fetch JSON
                  │ extract_bp_bq.py│──────────▶│              ┌─────┴──────┐
                  │ (Koti-only)     │           │              │ api_bq.py  │
                  └─────────────────┘           │              │ Cloud Func │
                                                │              └────────────┘
                                          ┌─────┴──────┐
                                          │bot_telegram │
                                          │.py          │
                                          │Gemini + BQ  │
                                          └────────────┘
```

**Fluxo (3x/dia — 5h, 12h, 18h BRT):**
1. `omie_sync_bq.py` coleta API Omie → BigQuery via MERGE incremental
2. `extract_bp_bq.py` extrai planilha BP → `orcamento_dre` (Koti-only)
3. Cloud Function serve JSON, Dashboard faz fetch, Bot responde perguntas

---

## 2. Arquivos

### Pipeline

| Arquivo | Descrição |
|---------|-----------|
| `omie_sync_bq.py` | Coleta API Omie → BigQuery via MERGE. Cria tabelas via `ensure_tables()` |
| `extract_bp_bq.py` | Planilha BP → `orcamento_dre` (Koti-only) |
| `bq_schema.sql` | DDL de referência |
| `.github/workflows/sync_omie_bq.yml` | 3x/dia (5h, 12h, 18h BRT) com retry |
| `requirements_bq.txt` | Deps do pipeline |

### Dashboard + API

| Arquivo | Descrição |
|---------|-----------|
| `dashboard_bq.html` | 8 abas, Chart.js. GitHub Pages → fetch Cloud Function |
| `api_bq.py` | Cloud Function: `/` serve HTML (local), `/api/dashboard` serve JSON. Horários em BRT |
| `main.py` | Entry point Cloud Function |
| `requirements.txt` | Deps Cloud Function |
| `index.html` | Redirect → `dashboard_bq.html` |

### Bot Telegram

| Arquivo | Descrição |
|---------|-----------|
| `bot_telegram.py` | NL→SQL via Gemini, busca fuzzy, análise financeira |
| `requirements_bot.txt` | Deps do bot |

### Koti-Only

| Arquivo | Descrição |
|---------|-----------|
| `extract_bp_bq.py` | DRE_MAP com linhas fixas da planilha Koti |
| `BP.xlsx` | Planilha Business Plan 2026 |

### Legado (descontinuar)

`dashboard_omie.html`, `omie_sync.py`, `extract_orcamento.py`, `encrypt_data.py`, `.github/workflows/sync_omie.yml`

---

## 3. Sync Incremental (MERGE)

Puxa tudo da API Omie mas só escreve o que mudou no BigQuery.

| Tabela | Método | Key | Campos comparados |
|--------|:------:|-----|-------------------|
| `lancamentos` | MERGE | `id` | valor, status, data_vencimento, data_pagamento, categoria, projeto, cliente |
| `categorias` | MERGE | `codigo` | nome, grupo |
| `projetos` | MERGE | `id` | nome |
| `clientes` | MERGE | `id` | nome_fantasia, razao_social, estado, ativo |
| `saldos_bancarios` | TRUNCATE | — | Snapshot D-1 |
| `vendas_pedidos` | TRUNCATE | — | Sem key estável |
| `historico_saldos` | APPEND | — | Dedup via view |
| `sync_log` | APPEND | — | Log |

**Resultado típico**: `23 novos, 34 atualizados, 0 removidos, 10.669 iguais`

**Frequência**: 5h, 12h, 18h BRT (GitHub Actions) + manual

---

## 4. BigQuery Schema

**Projeto**: `dashboard-koti-omie` | **Dataset por cliente** (ex: `studio_koti`)

| # | Tabela | Registros (Koti) |
|:-:|--------|:----------------:|
| 1 | `lancamentos` | ~10.700 |
| 2 | `saldos_bancarios` | ~17 |
| 3 | `historico_saldos` | acumula |
| 4 | `categorias` | ~142 |
| 5 | `projetos` | ~214 |
| 6 | `clientes` | ~1.837 |
| 7 | `vendas_pedidos` | ~673 |
| 8 | `orcamento_dre` | ~336 |
| 9 | `sync_log` | acumula |
| — | `v_historico_saldos` (view) | dedup |

### Lógica de datas (lancamentos)

| Status | Campo `data` no JSON | Campo no BQ |
|--------|---------------------|-------------|
| PAGO, RECEBIDO | Data real do pagamento | `data_pagamento` (extraído de `info.dAlt` da API Omie) |
| A VENCER, ATRASADO, VENCE HOJE | Data de vencimento | `data_vencimento` |

Isso garante que "quanto paguei em março" inclui pagamentos feitos em março mesmo que o vencimento fosse em outro mês.

### Campos — lancamentos

`id`, `tipo` (entrada/saida), `valor`, `status`, `data_vencimento`, `data_emissao`, `data_pagamento`, `numero_documento`, `categoria_codigo`, `categoria_nome`, `categoria_grupo`, `projeto_id`, `projeto_nome`, `cliente_id`, `cliente_nome`, `conta_corrente_id`, `is_faturamento_direto`, `sync_timestamp`, `sync_date`

---

## 5. Dashboard — 8 Abas

| # | Aba | Genérica? |
|:-:|-----|:---------:|
| 1 | **Visão Geral** — KPIs, saldo por conta, fluxo mensal, top categorias | Sim |
| 2 | **Fluxo de Caixa** — KPIs realizado/a realizar, gráficos por grupo, custos stacked Direto/SG&A/Outros (com valores nas barras), tabela Realizado \| A Realizar \| Total ou mês a mês | Sim |
| 3 | **Financeiro** — Receita vs custo vs SG&A, resultado mensal, contas a receber/pagar | Sim |
| 4 | **Conciliação** — Cards por conta (OK/Atenção/Pendente), evolução mensal/diário | Sim |
| 5 | **Vendas** — Total, pedidos, ticket médio, por etapa, top produtos | Sim |
| 6 | **Clientes** — Total, ativos, PF/PJ, por estado | Sim |
| 7 | **Projetos** — Receita vs custo, busca, tabela com margem | Sim |
| 8 | **Real vs Orçado** — Régua Jan-Dez, receita/EBITDA, waterfall, DRE comparativo | Koti-only |

---

## 6. Bot Telegram (@Kotifin_bot)

### Perguntas simples (NL → SQL)

O bot converte linguagem natural em SQL via Gemini e retorna dados formatados.

**Regras de negócio no prompt:**
- "faturei" / "NF" → entradas com status RECEBIDO
- "paguei" → saídas com status PAGO, filtra por `data_pagamento`
- "a pagar" / "a receber" → filtra por `data_vencimento`
- "projetos" (sem nome) → agrupa por `projeto_nome`
- "relação" → lista detalhada

### Busca fuzzy de nomes

Nomes de fornecedores/clientes são resolvidos **antes** de gerar o SQL:
1. Extrai palavras da pergunta que não são stopwords (200+ termos financeiros filtrados)
2. Gera fragmentos de 4 letras (ex: "castini" → "cast", "asti", "stin", "tini")
3. Busca no BigQuery com LIKE por cada fragmento
4. Se encontra match, adiciona `[CONTEXTO: 'castini' = 'NORTE SUL INDUSTRIA DE MOVEIS LTDA (Casttini)']` ao prompt
5. Gemini gera SQL com o nome correto

Se a query retorna 0 resultados, sugere nomes similares encontrados.

### Análise financeira (/analise)

Roda 8 queries de uma vez e pede pro Gemini analisar como consultor financeiro:

| Query | Dados |
|-------|-------|
| Resumo mensal | Receita vs despesa últimos 6 meses |
| Saldos | Todas as contas bancárias |
| A receber | Total, qtd, atrasados |
| A pagar | Total, qtd, atrasados |
| Top despesas | Top 10 categorias (3 meses) |
| Top clientes | Top 5 por receita (3 meses) |
| Margem projetos | Top 10 receita vs custo |
| Orçamento | Real vs BP (linhas nível 0) |

**Análise estruturada**: saúde financeira, pontos de atenção, oportunidades.

Ativado por `/analise` ou automaticamente por perguntas como "como está a saúde financeira?" ou "quais oportunidades para melhorar?".

### Comandos

| Comando | Ação |
|---------|------|
| `/start` | Menu com exemplos |
| `/saldo` | Saldos bancários |
| `/analise` | Análise financeira completa |
| `/analise [pergunta]` | Análise focada |
| `/status` | Último sync |

### Detalhes técnicos
- **Modelo**: Gemini 2.5 Flash (`google-genai` SDK, API key do projeto GCP com billing)
- **SQL safety**: só SELECT, só dataset autorizado, timeout 15s, limit 20
- **Respostas**: max 4000 chars, split em múltiplas mensagens se maior

---

## 7. Koti-Specific

Marcadas com `# ⚡ KOTI-SPECIFIC`. Para novo cliente, buscar e adaptar.

| Item | Arquivo | Novo cliente |
|------|---------|--------------|
| `CONTAS_IGNORAR = {8754849088}` | `omie_sync_bq.py` | Adaptar ou esvaziar |
| `is_faturamento_direto` | `omie_sync_bq.py` | Remover ou adaptar |
| `DRE_MAP` + `BP.xlsx` | `extract_bp_bq.py` | Ajustar linhas/ano ou remover |
| `PASS_HASH` | `dashboard_bq.html` | `echo -n "senha" \| shasum -a 256` |

---

## 8. Novo Cliente — Checklist

```bash
# 1. BigQuery (tabelas criadas automaticamente no primeiro sync)
bq mk --dataset --location=US dashboard-koti-omie:nome_cliente

# 2. Repo: fork, Secrets (OMIE_APP_KEY/SECRET, GCP_PROJECT_ID, GCP_SA_KEY),
#    BQ_DATASET no workflow, adaptar itens Koti-specific

# 3. API Cloud Function
gcloud functions deploy api_nome_cliente \
  --gen2 --runtime python311 --trigger-http --allow-unauthenticated \
  --entry-point api_dashboard --source . \
  --set-env-vars GCP_PROJECT_ID=dashboard-koti-omie,BQ_DATASET=nome_cliente \
  --region us-central1 --memory 512MB --timeout 60s

# 4. Dashboard: copiar HTML, atualizar PASS_HASH, GitHub Pages
# 5. Bot: criar no @BotFather, deploy com novo token e BQ_DATASET
```

---

## 9. Variáveis de Ambiente

| Variável | Usado por | Obrigatória |
|----------|-----------|:-----------:|
| `OMIE_APP_KEY` / `OMIE_APP_SECRET` | sync | Sim |
| `GCP_PROJECT_ID` | todos | Sim |
| `BQ_DATASET` | todos | Não (default: `studio_koti`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | local | Sim (local) |
| `TELEGRAM_BOT_TOKEN` | bot | Sim (bot) |
| `GEMINI_API_KEY` | bot | Sim (bot) |
| `AUTHORIZED_CHAT_IDS` | bot | Não |

---

## 10. Troubleshooting

| Problema | Solução |
|----------|---------|
| Cloud Function 503 | `--memory 512MB` |
| Cloud Function 403 | Roles: `bigquery.dataViewer` + `bigquery.jobUser` |
| Dados desatualizados | Sync 3x/dia. Manual: `python omie_sync_bq.py` |
| Valor pago não bate com Omie | Filtro deve usar `data_pagamento`, não `data_vencimento` |
| Bot não acha fornecedor | Busca fuzzy por fragmentos. Verificar stopwords |
| Bot confunde palavras comuns com nomes | 200+ stopwords financeiras filtradas |
| Aba Orçamento vazia | Koti-only: `extract_bp_bq.py` + `BP.xlsx` |
| Deploy falha billing | Habilitar em console.cloud.google.com/billing |

---

## 11. Custos

| Item | Custo |
|------|:-----:|
| BigQuery, Cloud Functions, GitHub Actions/Pages | R$ 0 (free tier) |
| Gemini API (bot) | ~R$ 0-5/mês |
| **Budget GCP configurado** | **US$ 4/mês (~R$ 20)**, alertas em 50%, 80%, 100% |
