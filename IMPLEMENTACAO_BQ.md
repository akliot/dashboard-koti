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
                                                │              │ CORS restr.│
                                                │              └────────────┘
                                          ┌─────┴──────┐
                                          │bot_telegram │
                                          │.py          │
                                          │Claude / BQ  │
                                          └────────────┘
```

**Fluxo (3x/dia — 5h, 12h, 18h BRT):**
1. `omie_sync_bq.py` coleta API Omie → BigQuery via MERGE incremental
2. `extract_bp_bq.py` extrai planilha BP → `orcamento_dre` (Koti-only)
3. Cloud Function serve JSON (CORS restritivo), Dashboard faz fetch, Bot responde perguntas
4. Se sync falha → alerta no Telegram via `ADMIN_CHAT_ID`

---

## 2. Arquivos

### Pipeline

| Arquivo | Descrição |
|---------|-----------|
| `omie_sync_bq.py` | Coleta API Omie → BigQuery via MERGE. `ensure_tables()`, alerta Telegram em falha |
| `extract_bp_bq.py` | Planilha BP → `orcamento_dre` (Koti-only) |
| `bq_schema.sql` | DDL de referência |
| `.github/workflows/sync_omie_bq.yml` | 3x/dia (5h, 12h, 18h BRT) com retry |
| `requirements_bq.txt` | Deps do pipeline |

### Dashboard + API

| Arquivo | Descrição |
|---------|-----------|
| `dashboard_bq.html` | 8 abas, Chart.js, regime caixa/competência. GitHub Pages → fetch Cloud Function |
| `api_bq.py` | Cloud Function: `/api/dashboard` serve JSON. CORS restritivo (só github.io + localhost) |
| `main.py` | Entry point Cloud Function |
| `requirements.txt` | Deps Cloud Function |
| `index.html` | Redirect → `dashboard_bq.html` |

### Bot Telegram

| Arquivo | Descrição |
|---------|-----------|
| `bot_telegram.py` | NL→SQL via Claude Haiku / Gemini (auto-detect), memória de conversa, busca fuzzy, `/analise` |
| `requirements_bot.txt` | Deps do bot (anthropic, google-genai, python-telegram-bot) |

### Testes

| Arquivo | Testes | Resultado |
|---------|:------:|:---------:|
| `test_pipeline.py` | 45 | 100% |
| `test_api.py` | 46 | 100% |
| `test_bot.py` | 54 | 91% |
| **Total** | **145** | — |

### Koti-Only

| Arquivo | Descrição |
|---------|-----------|
| `extract_bp_bq.py` | DRE_MAP com linhas fixas da planilha Koti |

> `BP.xlsx` removido do repo e histórico Git (dados financeiros sensíveis). Manter local ou no Google Drive.

### Legado (descontinuar)

`dashboard_omie.html`, `omie_sync.py`, `extract_orcamento.py`, `encrypt_data.py`, `.github/workflows/sync_omie.yml`

---

## 3. Setup GCP do Zero

### 3.1 Criar projeto e habilitar APIs

```bash
gcloud auth login
gcloud projects create dashboard-koti-omie --name="Dashboard Omie"
gcloud config set project dashboard-koti-omie

gcloud services enable bigquery.googleapis.com
gcloud services enable cloudfunctions.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable artifactregistry.googleapis.com
gcloud services enable aiplatform.googleapis.com
```

### 3.2 Service Account

```bash
gcloud iam service-accounts create omie-sync --display-name="Omie Sync Pipeline"

gcloud projects add-iam-policy-binding dashboard-koti-omie \
  --member="serviceAccount:omie-sync@dashboard-koti-omie.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataEditor"

gcloud projects add-iam-policy-binding dashboard-koti-omie \
  --member="serviceAccount:omie-sync@dashboard-koti-omie.iam.gserviceaccount.com" \
  --role="roles/bigquery.jobUser"

gcloud iam service-accounts keys create gcp-key.json \
  --iam-account=omie-sync@dashboard-koti-omie.iam.gserviceaccount.com

cat gcp-key.json | base64 | pbcopy  # → GitHub Secret GCP_SA_KEY
```

### 3.3 Criar dataset

```bash
bq mk --dataset --location=US dashboard-koti-omie:studio_koti
```

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

### 4.1 Schema SQL completo

Ver `bq_schema.sql` para DDL com OPTIONS/descriptions. Tabelas criadas automaticamente por `ensure_tables()`.

### 4.2 Mapeamento API Omie → BigQuery (lancamentos)

| Campo BigQuery | Campo API Omie | Transformação |
|---------------|----------------|---------------|
| `id` | `codigo_lancamento_omie` | direto |
| `tipo` | — | `'entrada'` ou `'saida'` |
| `valor` | `valor_documento` | `float()` |
| `status` | `status_titulo` | `.upper()` |
| `data_vencimento` | `data_vencimento` | DD/MM/YYYY → YYYY-MM-DD |
| `data_emissao` | `data_emissao` | DD/MM/YYYY → YYYY-MM-DD |
| `data_pagamento` | `dDtPagamento` (via `ListarMovimentos`) | Data real via Movimentos Financeiros (99.99% match). Fallback: `data_previsao` |
| `data_previsao` | `data_previsao` | Dia útil previsto de pagamento |
| `categoria_codigo` | `codigo_categoria` | direto |
| `categoria_nome` | — | lookup via `cat_map` |
| `cliente_nome` | — | lookup via `cli_map` (bulk) |
| `is_faturamento_direto` | — | ⚡ Entrada: categoria contém "Faturamento Direto". Saída: `numero_documento` OU `numero_documento_fiscal` contém "FD" |
| `modalidade` | — | ⚡ "FD" se is_faturamento_direto, senão "SK" |

### 4.3 Lógica de datas e regimes contábeis

**No BigQuery (campo `data_pagamento`):**

| Status | `data_pagamento` | Fonte |
|--------|-----------------|-------|
| PAGO, RECEBIDO | Data real do pagamento | `ListarMovimentos` (financas/mf) → `dDtPagamento` via `nCodTitulo` (99.99% match) |
| A VENCER, ATRASADO, VENCE HOJE | NULL | — |

> A API `ListarContasReceber/Pagar` não expõe data real de pagamento. O `ListarMovimentos` é a única fonte confiável — `nCodTitulo` = `codigo_lancamento_omie` (link direto).

**Na API JSON (campo `data`):**

| Status | `data` no JSON | Usado por |
|--------|---------------|-----------|
| PAGO, RECEBIDO | `data_pagamento` | Dashboard (regime caixa) |
| Pendentes | `data_vencimento` | Dashboard (regime caixa) |

**No Dashboard (caixa vs competência):**

| Regime | Abas | Filtro | Exclui ATRASADO? |
|--------|------|--------|:----------------:|
| **Caixa** | 1 (Visão Geral), 2 (Fluxo), 4 (Conciliação) | `l.data` (pagamento/vencimento) | Não |
| **Competência** | 3 (Financeiro), 5 (Vendas), 7 (Projetos) | `l.data_vencimento` | Sim |

`aplicarFiltros()` cria dois arrays: `lancamentos` (caixa) e `lancamentos_competencia`. `compute()` e `computeCompetencia()` calculam métricas separadas.

---

## 5. Sync Incremental (MERGE)

Puxa tudo da API Omie mas só escreve o que mudou no BigQuery.

**Etapa extra**: `coletar_movimentos_financeiros()` pagina `ListarMovimentos` (financas/mf) para obter datas reais de pagamento via `nCodTitulo` → `dDtPagamento`. ~229 páginas, ~10K datas.

| Tabela | Método | Key | Campos comparados |
|--------|:------:|-----|-------------------|
| `lancamentos` | MERGE | `id` | valor, status, data_vencimento, data_pagamento, data_previsao, modalidade, categoria, projeto, cliente |
| `categorias` | MERGE | `codigo` | nome, grupo |
| `projetos` | MERGE | `id` | nome |
| `clientes` | MERGE | `id` | nome_fantasia, razao_social, estado, ativo |
| `saldos_bancarios` | TRUNCATE | — | Snapshot D-1 |
| `vendas_pedidos` | TRUNCATE | — | Sem key estável |
| `historico_saldos` | APPEND | — | Dedup via view |
| `sync_log` | APPEND | — | Log |

**Resultado típico**: `23 novos, 34 atualizados, 0 removidos, 10.669 iguais`

**Frequência**: 5h, 12h, 18h BRT + manual

**Alerta de falha**: Se sync falha, `notify_sync_failed()` envia mensagem no Telegram via `ADMIN_CHAT_ID`.

> **Rate limits API Omie**: `time.sleep(0.05)` entre chamadas, retry com backoff de 2s × tentativa, timeout 60s.

---

## 6. Dashboard — 8 Abas

| # | Aba | Regime | Genérica? |
|:-:|-----|:------:|:---------:|
| 1 | **Visão Geral** — KPIs, saldo por conta (toggle D-1/período), entradas vs saídas stacked FD/SK com data labels, variação de caixa (linha), saldo acumulado real (historico_saldos), top categorias | Caixa | Sim |
| 2 | **Fluxo de Caixa** — KPIs realizado/a realizar, custos stacked, tabela Realizado \| A Realizar \| Total | Caixa | Sim |
| 3 | **Financeiro** — Receita vs custo vs SG&A, resultado mensal, contas a receber/pagar | Competência | Sim |
| 4 | **Conciliação** — Cards por conta, evolução mensal/diário | Caixa | Sim |
| 5 | **Vendas** — Total, pedidos, ticket médio, por etapa, top produtos | Competência | Sim |
| 6 | **Clientes** — Total, ativos, PF/PJ, por estado | — | Sim |
| 7 | **Projetos** — Receita vs custo, busca, tabela com margem | Competência | Sim |
| 8 | **Real vs Orçado** — Régua Jan-Dez, receita/EBITDA, waterfall, DRE comparativo | — | Koti-only |

---

## 7. API Cloud Function

### Segurança

**CORS restritivo** — só aceita requests de:
- `https://akliot.github.io` (produção)
- `http://localhost:8080` (dev)

Origins desconhecidos recebem fallback para `akliot.github.io`. Servidor local mantém `*` para conveniência.

### Deploy

```bash
gcloud functions deploy api_dashboard \
  --gen2 --runtime python311 --trigger-http --allow-unauthenticated \
  --entry-point api_dashboard --source . \
  --set-env-vars GCP_PROJECT_ID=dashboard-koti-omie,BQ_DATASET=studio_koti \
  --region us-central1 --memory 512MB --timeout 60s
```

---

## 8. Bot Telegram (@Kotifin_bot)

### LLM Provider

Auto-detect: `ANTHROPIC_API_KEY` → **Claude Haiku 4.5**, senão `GEMINI_API_KEY` → Gemini 2.5 Flash. Retry com backoff em 429.

### Recursos

- **Memória de conversa**: últimas 5 interações por chat
- **Busca fuzzy**: fragmentos de 4 letras para variações ortográficas
- **Desambiguação**: 200+ stopwords financeiras
- **Regras BQ**: `STRING_AGG`, `EXTRACT(YEAR FROM)`, etc.
- **Regra de contexto**: follow-ups copiam filtros de data do SQL anterior
- **Exemplos SQL**: 5 exemplos concretos no prompt para guiar o LLM
- **Ano corrente**: mês sem ano → sempre assume ano corrente

### Análise financeira (/analise)

8 queries simultâneas + LLM como consultor financeiro.

### Comandos

| Comando | Ação |
|---------|------|
| `/start` | Menu com exemplos |
| `/saldo` | Saldos bancários |
| `/analise` | Análise financeira completa |
| `/status` | Último sync |

---

## 9. Testes

| Suite | Arquivo | Testes | Cobertura |
|-------|---------|:------:|-----------|
| Pipeline | `test_pipeline.py` | 45 (100%) | parse_date, categorias, CONTAS_IGNORAR, FD, data_pagamento, status, grupo, SQL safety |
| API | `test_api.py` | 46 (100%) | datas, tbl, data_ref logic, CORS (6 cenários), build_json (17 testes com BQ live) |
| Bot | `test_bot.py` | 54 (91%) | 17 cenários: caixa, fornecedores, projetos, faturamento, categorias, comparações, follow-ups, typos, perguntas ambíguas |

**Rodar testes:**
```bash
python3 test_pipeline.py -v           # unitários pipeline
GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-key.json python3 test_api.py -v  # API (precisa BQ)
export $(cat .env | grep -v '^#' | xargs) && python3 test_bot.py          # bot (precisa LLM)
```

---

## 10. Koti-Specific

Marcadas com `# ⚡ KOTI-SPECIFIC`.

| Item | Arquivo | Novo cliente |
|------|---------|--------------|
| `CONTAS_IGNORAR = {8754849088}` | `omie_sync_bq.py` | Adaptar ou esvaziar |
| `is_faturamento_direto` + `modalidade` (SK/FD) | `omie_sync_bq.py` | Entrada: categoria "Faturamento Direto". Saída: "FD" em `numero_documento` ou `numero_documento_fiscal`. Remover ou adaptar para cliente |
| `DRE_MAP` | `extract_bp_bq.py` | Ajustar linhas/ano ou remover |
| `PASS_HASH` | `dashboard_bq.html` | `echo -n "senha" \| shasum -a 256` |

> `BP.xlsx` removido do repo (dados sensíveis). Manter local.

---

## 11. Novo Cliente — Checklist

```bash
# 1. BigQuery
bq mk --dataset --location=US dashboard-koti-omie:nome_cliente

# 2. Repo: fork, Secrets, BQ_DATASET, adaptar Koti-specific

# 3. API Cloud Function
gcloud functions deploy api_nome_cliente \
  --gen2 --runtime python311 --trigger-http --allow-unauthenticated \
  --entry-point api_dashboard --source . \
  --set-env-vars GCP_PROJECT_ID=dashboard-koti-omie,BQ_DATASET=nome_cliente \
  --region us-central1 --memory 512MB --timeout 60s

# 4. Dashboard: copiar HTML, PASS_HASH, CORS, GitHub Pages
# 5. Bot: @BotFather, novo token, BQ_DATASET
```

---

## 12. Variáveis de Ambiente

| Variável | Usado por | Obrigatória |
|----------|-----------|:-----------:|
| `OMIE_APP_KEY` / `OMIE_APP_SECRET` | sync | Sim |
| `GCP_PROJECT_ID` | todos | Sim |
| `BQ_DATASET` | todos | Não (default: `studio_koti`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | local | Sim (local) |
| `TELEGRAM_BOT_TOKEN` | bot + alerta sync | Sim (bot) |
| `ANTHROPIC_API_KEY` | bot (Claude) | Sim se usar Claude |
| `GEMINI_API_KEY` | bot (Gemini) | Sim se usar Gemini |
| `ADMIN_CHAT_ID` | alerta de falha sync | Não (mas recomendado) |
| `AUTHORIZED_CHAT_IDS` | bot | Não |

---

## 13. Queries SQL Úteis

### Fluxo de caixa mensal

```sql
SELECT FORMAT_DATE('%Y-%m', data_pagamento) AS mes,
  SUM(CASE WHEN tipo='entrada' AND status='RECEBIDO' THEN valor ELSE 0 END) AS entradas,
  SUM(CASE WHEN tipo='saida' AND status='PAGO' THEN valor ELSE 0 END) AS saidas
FROM `studio_koti.lancamentos`
WHERE data_pagamento >= '2026-01-01'
GROUP BY mes ORDER BY mes;
```

### Margem por projeto

```sql
SELECT projeto_nome,
  ROUND(SUM(CASE WHEN tipo='entrada' THEN valor ELSE 0 END),2) AS receita,
  ROUND(SUM(CASE WHEN tipo='saida' THEN valor ELSE 0 END),2) AS custo,
  ROUND(SUM(CASE WHEN tipo='entrada' THEN valor ELSE -valor END),2) AS margem
FROM `studio_koti.lancamentos`
WHERE status IN ('PAGO','RECEBIDO') AND projeto_nome != 'Sem projeto'
GROUP BY projeto_nome ORDER BY margem DESC;
```

### Contas a receber próximos 30 dias

```sql
SELECT cliente_nome, ROUND(valor,2) as valor, data_vencimento, status
FROM `studio_koti.lancamentos`
WHERE tipo='entrada' AND status IN ('A VENCER','ATRASADO','VENCE HOJE')
  AND data_vencimento BETWEEN CURRENT_DATE() AND DATE_ADD(CURRENT_DATE(), INTERVAL 30 DAY)
ORDER BY data_vencimento;
```

### Real vs Orçado — EBITDA

```sql
SELECT mes, ROUND(valor_real,2) as real, ROUND(valor_bp,2) as bp, ROUND(variacao_pct,1) as var_pct
FROM `studio_koti.orcamento_dre`
WHERE label='EBITDA' AND mes_com_real=TRUE ORDER BY mes;
```

### Faturamento Direto vs Studio Koti (⚡ Koti-specific)

```sql
SELECT FORMAT_DATE('%Y-%m', data_pagamento) AS mes,
  CASE WHEN is_faturamento_direto THEN 'Faturamento Direto' ELSE 'Studio Koti' END AS modalidade,
  ROUND(SUM(valor),2) AS total, COUNT(*) AS qtd
FROM `studio_koti.lancamentos`
WHERE status IN ('PAGO','RECEBIDO')
GROUP BY mes, modalidade ORDER BY mes, modalidade;
```

---

## 14. Monitoramento

### Alerta de sync (BigQuery)

```sql
SELECT
  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(finished_at), HOUR) AS horas_desde_ultimo_sync,
  COUNTIF(status='failed' AND started_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)) AS falhas_7_dias
FROM `studio_koti.sync_log`;
```

### Alerta de sync (Telegram)

`notify_sync_failed()` em `omie_sync_bq.py` envia mensagem automática no Telegram quando sync falha. Configurar `TELEGRAM_BOT_TOKEN` + `ADMIN_CHAT_ID`.

**Regras visuais:**
- 🟢 Último sync < 8h
- 🟡 Último sync 8h–24h
- 🔴 Último sync > 24h ou 2+ falhas na semana

---

## 15. Troubleshooting

| Problema | Solução |
|----------|---------|
| Cloud Function 503 | `--memory 512MB` |
| Cloud Function 403 | Roles: `bigquery.dataViewer` + `bigquery.jobUser` |
| CORS bloqueado | Verificar origin em `ALLOWED_ORIGINS` no `api_bq.py` |
| Dados desatualizados | Sync 3x/dia. Manual: `python omie_sync_bq.py` |
| Valor pago não bate com Omie | Filtro deve usar `data_pagamento`, não `data_vencimento` |
| Aba Financeiro ≠ Fluxo de Caixa | Correto: Financeiro usa competência, Fluxo usa caixa |
| Bot não acha fornecedor | Busca fuzzy por fragmentos. Verificar stopwords |
| Bot gera SQL MySQL | Prompt tem regras BQ: STRING_AGG, EXTRACT, FORMAT_DATE |
| Aba Orçamento vazia | Koti-only: `extract_bp_bq.py` + `BP.xlsx` (local) |
| Deploy falha billing | Habilitar em console.cloud.google.com/billing |
| BP.xlsx no repo | Removido do histórico via `git filter-repo`. No `.gitignore` |

---

## 16. Custos

| Item | Custo |
|------|:-----:|
| BigQuery, Cloud Functions, GitHub Actions/Pages | R$ 0 (free tier) |
| Claude API (bot) | ~US$ 2-5/mês |
| Gemini API (bot, se usado) | ~R$ 0-5/mês |
| **Budget GCP configurado** | **US$ 4/mês (~R$ 20)**, alertas em 50%, 80%, 100% |
