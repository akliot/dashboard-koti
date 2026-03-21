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
                                          │Claude / BQ  │
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
| `bot_telegram.py` | NL→SQL via Claude Haiku / Gemini (auto-detect), busca fuzzy, análise financeira |
| `requirements_bot.txt` | Deps do bot |
| `test_bot.py` | Stress test — 33 cenários simulando o dono da empresa |

### Koti-Only

| Arquivo | Descrição |
|---------|-----------|
| `extract_bp_bq.py` | DRE_MAP com linhas fixas da planilha Koti |
| `BP.xlsx` | Planilha Business Plan 2026 |

### Legado (descontinuar)

`dashboard_omie.html`, `omie_sync.py`, `extract_orcamento.py`, `encrypt_data.py`, `.github/workflows/sync_omie.yml`

---

## 3. Setup GCP do Zero

### 3.1 Criar projeto e habilitar APIs

```bash
gcloud auth login
gcloud projects create dashboard-koti-omie --name="Dashboard Omie"
gcloud config set project dashboard-koti-omie

# APIs necessárias
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

# Permissões
gcloud projects add-iam-policy-binding dashboard-koti-omie \
  --member="serviceAccount:omie-sync@dashboard-koti-omie.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataEditor"

gcloud projects add-iam-policy-binding dashboard-koti-omie \
  --member="serviceAccount:omie-sync@dashboard-koti-omie.iam.gserviceaccount.com" \
  --role="roles/bigquery.jobUser"

# Gerar chave JSON
gcloud iam service-accounts keys create gcp-key.json \
  --iam-account=omie-sync@dashboard-koti-omie.iam.gserviceaccount.com

# Encodar para GitHub Secret
cat gcp-key.json | base64 | pbcopy
```

> Deletar `gcp-key.json` local após salvar no GitHub Secret.

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

```sql
CREATE TABLE IF NOT EXISTS `studio_koti.lancamentos` (
  id                    INT64       NOT NULL OPTIONS(description="ID do lançamento no Omie"),
  tipo                  STRING      NOT NULL OPTIONS(description="'entrada' (receber) ou 'saida' (pagar)"),
  valor                 FLOAT64     NOT NULL OPTIONS(description="Valor do documento em R$"),
  status                STRING      OPTIONS(description="PAGO, RECEBIDO, A VENCER, ATRASADO, VENCE HOJE, CANCELADO"),
  data_vencimento       DATE        OPTIONS(description="Data de vencimento do título"),
  data_emissao          DATE        OPTIONS(description="Data de emissão do documento"),
  data_pagamento        DATE        OPTIONS(description="Data real de pagamento/recebimento (info.dAlt). NULL se pendente"),
  numero_documento      STRING      OPTIONS(description="Número do documento/NF"),
  categoria_codigo      STRING      OPTIONS(description="Código da categoria (ex: 1.01.02)"),
  categoria_nome        STRING      OPTIONS(description="Nome da categoria (ex: Marcenaria)"),
  categoria_grupo       STRING      OPTIONS(description="Grupo — 2 primeiros níveis (ex: 1.01)"),
  projeto_id            INT64       OPTIONS(description="ID do projeto/obra no Omie"),
  projeto_nome          STRING      OPTIONS(description="Nome do projeto/obra"),
  cliente_id            INT64       OPTIONS(description="ID do cliente/fornecedor"),
  cliente_nome          STRING      OPTIONS(description="Nome fantasia ou razão social"),
  conta_corrente_id     INT64       OPTIONS(description="ID da conta corrente"),
  is_faturamento_direto BOOL        OPTIONS(description="⚡ KOTI-SPECIFIC: True se FD"),
  sync_timestamp        TIMESTAMP   NOT NULL,
  sync_date             DATE        NOT NULL
) PARTITION BY sync_date CLUSTER BY tipo, categoria_grupo;

CREATE TABLE IF NOT EXISTS `studio_koti.saldos_bancarios` (
  conta_id INT64 NOT NULL, conta_nome STRING NOT NULL, conta_tipo STRING,
  saldo FLOAT64 NOT NULL, saldo_conciliado FLOAT64 NOT NULL, diferenca FLOAT64 NOT NULL,
  data_referencia DATE NOT NULL, sync_timestamp TIMESTAMP NOT NULL, sync_date DATE NOT NULL
) PARTITION BY sync_date;

CREATE TABLE IF NOT EXISTS `studio_koti.historico_saldos` (
  conta_id INT64 NOT NULL, conta_nome STRING NOT NULL, data_referencia DATE NOT NULL,
  label STRING, saldo_atual FLOAT64 NOT NULL, saldo_conciliado FLOAT64 NOT NULL,
  diferenca FLOAT64 NOT NULL, tipo STRING NOT NULL OPTIONS(description="'mensal' ou 'diario'"),
  sync_timestamp TIMESTAMP NOT NULL, sync_date DATE NOT NULL
) PARTITION BY sync_date CLUSTER BY conta_id, tipo;

CREATE TABLE IF NOT EXISTS `studio_koti.categorias` (
  codigo STRING NOT NULL, nome STRING NOT NULL, grupo STRING, sync_timestamp TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS `studio_koti.projetos` (
  id INT64 NOT NULL, nome STRING NOT NULL, sync_timestamp TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS `studio_koti.clientes` (
  id INT64 NOT NULL, nome_fantasia STRING, razao_social STRING, estado STRING,
  ativo BOOL, pessoa_fisica BOOL, data_cadastro DATE, sync_timestamp TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS `studio_koti.vendas_pedidos` (
  pedido_id INT64, valor_mercadorias FLOAT64 NOT NULL, etapa STRING,
  data_previsao DATE, produto_descricao STRING, produto_quantidade FLOAT64,
  produto_valor_total FLOAT64, sync_timestamp TIMESTAMP NOT NULL, sync_date DATE NOT NULL
) PARTITION BY sync_date;

CREATE TABLE IF NOT EXISTS `studio_koti.orcamento_dre` (
  label STRING NOT NULL, section STRING NOT NULL, level INT64 NOT NULL,
  mes STRING NOT NULL, valor_real FLOAT64, valor_bp FLOAT64,
  variacao_pct FLOAT64, mes_com_real BOOL, sync_timestamp TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS `studio_koti.sync_log` (
  sync_id STRING NOT NULL, started_at TIMESTAMP NOT NULL, finished_at TIMESTAMP,
  status STRING NOT NULL, duration_seconds INT64, lancamentos_count INT64,
  saldos_count INT64, clientes_count INT64, projetos_count INT64,
  categorias_count INT64, error_message STRING, is_incremental BOOL
);

-- View dedup para historico_saldos
CREATE OR REPLACE VIEW `studio_koti.v_historico_saldos` AS
SELECT * EXCEPT(rn) FROM (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY conta_id, data_referencia, tipo
    ORDER BY sync_timestamp DESC
  ) AS rn
  FROM `studio_koti.historico_saldos`
) WHERE rn = 1;
```

### 4.2 Mapeamento API Omie → BigQuery (lancamentos)

| Campo BigQuery | Campo API Omie | Transformação |
|---------------|----------------|---------------|
| `id` | `codigo_lancamento_omie` | direto |
| `tipo` | — | `'entrada'` (receber) ou `'saida'` (pagar) |
| `valor` | `valor_documento` | `float()` |
| `status` | `status_titulo` | `.upper()` |
| `data_vencimento` | `data_vencimento` | DD/MM/YYYY → YYYY-MM-DD |
| `data_emissao` | `data_emissao` | DD/MM/YYYY → YYYY-MM-DD |
| `data_pagamento` | `info.dAlt` | DD/MM/YYYY → YYYY-MM-DD (só se PAGO/RECEBIDO) |
| `categoria_codigo` | `codigo_categoria` | direto |
| `categoria_nome` | — | lookup via `cat_map` |
| `categoria_grupo` | — | 2 primeiros níveis do código |
| `projeto_id` | `codigo_projeto` | direto |
| `projeto_nome` | — | lookup via `proj_map` |
| `cliente_id` | `codigo_cliente_fornecedor` | direto |
| `cliente_nome` | — | lookup via `cli_map` (bulk) |
| `numero_documento` | `numero_documento` | direto |
| `is_faturamento_direto` | — | ⚡ Entrada: categoria contém "Faturamento Direto". Saída: doc contém "FD" |

### 4.3 Lógica de datas

| Status | Campo `data` no JSON da API | Campo no BQ |
|--------|---------------------------|-------------|
| PAGO, RECEBIDO | Data real do pagamento | `data_pagamento` (de `info.dAlt`) |
| A VENCER, ATRASADO, VENCE HOJE | Data de vencimento | `data_vencimento` |

---

## 5. Sync Incremental (MERGE)

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

> **Rate limits API Omie**: `time.sleep(0.05)` entre chamadas, retry com backoff de 2s × tentativa, timeout 60s por request.

---

## 6. Dashboard — 8 Abas

| # | Aba | Genérica? |
|:-:|-----|:---------:|
| 1 | **Visão Geral** — KPIs, saldo por conta, fluxo mensal, top categorias | Sim |
| 2 | **Fluxo de Caixa** — KPIs realizado/a realizar, gráficos por grupo, custos stacked Direto/SG&A/Outros, tabela Realizado \| A Realizar \| Total ou mês a mês | Sim |
| 3 | **Financeiro** — Receita vs custo vs SG&A, resultado mensal, contas a receber/pagar | Sim |
| 4 | **Conciliação** — Cards por conta (OK/Atenção/Pendente), evolução mensal/diário | Sim |
| 5 | **Vendas** — Total, pedidos, ticket médio, por etapa, top produtos | Sim |
| 6 | **Clientes** — Total, ativos, PF/PJ, por estado | Sim |
| 7 | **Projetos** — Receita vs custo, busca, tabela com margem | Sim |
| 8 | **Real vs Orçado** — Régua Jan-Dez, receita/EBITDA, waterfall, DRE comparativo | Koti-only |

---

## 7. Bot Telegram (@Kotifin_bot)

### LLM Provider

Auto-detect: `ANTHROPIC_API_KEY` → Claude Haiku 4.5, senão `GEMINI_API_KEY` → Gemini 2.5 Flash.

### Perguntas simples (NL → SQL)

Converte linguagem natural em SQL BigQuery, executa, formata resposta.

**Recursos:**
- **Memória de conversa**: últimas 5 interações por chat — "E de castini?" após perguntar sobre kairos mantém contexto
- **Busca fuzzy**: fragmentos de 4 letras para pegar variações ortográficas (castini → Casttini)
- **Desambiguação**: 200+ stopwords financeiras evitam confundir "recebimentos" com nome de empresa
- **Regras BQ**: prompt instrui a usar `STRING_AGG` (não `GROUP_CONCAT`), `EXTRACT(YEAR FROM)` (não `YEAR()`), etc.
- **Regra de contexto**: follow-ups curtos ("E desse?", "Mostra detalhes") copiam filtros do SQL anterior

### Análise financeira (/analise)

Roda 8 queries de uma vez (receita mensal, saldos, a receber/pagar, top despesas, top clientes, margem projetos, orçamento) e pede pro LLM analisar como consultor financeiro.

### Comandos

| Comando | Ação |
|---------|------|
| `/start` | Menu com exemplos |
| `/saldo` | Saldos bancários |
| `/analise` | Análise financeira completa |
| `/analise [pergunta]` | Análise focada |
| `/status` | Último sync |

### Test suite

`test_bot.py`: 33 testes em 10 cenários simulando o dono. Resultado: **85% (28/33)** com Claude Haiku.

---

## 8. Koti-Specific

Marcadas com `# ⚡ KOTI-SPECIFIC`. Para novo cliente, buscar e adaptar.

| Item | Arquivo | Novo cliente |
|------|---------|--------------|
| `CONTAS_IGNORAR = {8754849088}` | `omie_sync_bq.py` | Adaptar ou esvaziar |
| `is_faturamento_direto` | `omie_sync_bq.py` | Remover ou adaptar |
| `DRE_MAP` + `BP.xlsx` | `extract_bp_bq.py` | Ajustar linhas/ano ou remover |
| `PASS_HASH` | `dashboard_bq.html` | `echo -n "senha" \| shasum -a 256` |

---

## 9. Novo Cliente — Checklist

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

## 10. Variáveis de Ambiente

| Variável | Usado por | Obrigatória |
|----------|-----------|:-----------:|
| `OMIE_APP_KEY` / `OMIE_APP_SECRET` | sync | Sim |
| `GCP_PROJECT_ID` | todos | Sim |
| `BQ_DATASET` | todos | Não (default: `studio_koti`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | local | Sim (local) |
| `TELEGRAM_BOT_TOKEN` | bot | Sim (bot) |
| `ANTHROPIC_API_KEY` | bot (Claude) | Sim se usar Claude |
| `GEMINI_API_KEY` | bot (Gemini) | Sim se usar Gemini |
| `AUTHORIZED_CHAT_IDS` | bot | Não |

---

## 11. Queries SQL Úteis

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

## 12. Monitoramento

### Alerta de sync

```sql
SELECT
  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(finished_at), HOUR) AS horas_desde_ultimo_sync,
  COUNTIF(status='failed' AND started_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)) AS falhas_7_dias
FROM `studio_koti.sync_log`;
```

**Regras de alerta:**
- 🟢 Último sync < 8h
- 🟡 Último sync 8h–24h
- 🔴 Último sync > 24h ou 2+ falhas na semana

---

## 13. Troubleshooting

| Problema | Solução |
|----------|---------|
| Cloud Function 503 | `--memory 512MB` |
| Cloud Function 403 | Roles: `bigquery.dataViewer` + `bigquery.jobUser` |
| Dados desatualizados | Sync 3x/dia. Manual: `python omie_sync_bq.py` |
| Valor pago não bate com Omie | Filtro deve usar `data_pagamento`, não `data_vencimento` |
| Bot não acha fornecedor | Busca fuzzy por fragmentos. Verificar stopwords |
| Bot confunde palavras comuns com nomes | 200+ stopwords financeiras filtradas |
| Bot gera SQL MySQL | Prompt tem regras BQ: STRING_AGG, EXTRACT, FORMAT_DATE |
| Aba Orçamento vazia | Koti-only: `extract_bp_bq.py` + `BP.xlsx` |
| Deploy falha billing | Habilitar em console.cloud.google.com/billing |

---

## 14. Custos

| Item | Custo |
|------|:-----:|
| BigQuery, Cloud Functions, GitHub Actions/Pages | R$ 0 (free tier) |
| Claude API (bot) | ~US$ 2-5/mês |
| Gemini API (bot, se usado) | ~R$ 0-5/mês |
| **Budget GCP configurado** | **US$ 4/mês (~R$ 20)**, alertas em 50%, 80%, 100% |
