# Dashboard Financeiro — Omie + BigQuery

**Projeto GCP**: `dashboard-koti-omie`
**Repositório**: https://github.com/akliot/dashboard-koti
**Dashboard (produção)**: https://akliot.github.io/dashboard-koti/
**API (produção)**: https://us-central1-dashboard-koti-omie.cloudfunctions.net/api_dashboard
**Bot Telegram**: @Kotifin_bot
**Última atualização**: 20/03/2026

> Sistema para empresas que usam Omie ERP. Primeiro cliente: **Studio Koti** (dataset `studio_koti`).

---

## 1. Arquitetura

```
┌──────────┐     ┌─────────────────┐     ┌──────────────┐     ┌────────────────┐
│ API Omie │────▶│ omie_sync_bq.py │────▶│   BigQuery    │────▶│ dashboard_bq   │
│ (ERP)    │     │ (GitHub Actions) │     │   (GCP)      │     │ .html (GitHub  │
└──────────┘     │ 3x/dia MERGE    │     └──────┬───────┘     │  Pages)        │
                 └─────────────────┘            │             └────────────────┘
                                                │                    ▲
                  ┌─────────────────┐           │                    │ fetch JSON
                  │ extract_bp_bq.py│──────────▶│              ┌─────┴──────┐
                  │ (⚡ Koti-only)   │           │              │ api_bq.py  │
                  └─────────────────┘           │              │ Cloud Func. │
                                                │              └────────────┘
                                                │
                                          ┌─────┴──────┐
                                          │bot_telegram │
                                          │.py (Gemini  │
                                          │ + BigQuery) │
                                          └────────────┘
```

**Fluxo diário (3x — 5h, 12h, 18h BRT):**
1. GitHub Actions roda `omie_sync_bq.py` com **MERGE incremental** — só atualiza o que mudou
2. `extract_bp_bq.py` extrai planilha BP → `orcamento_dre` (Koti-specific)
3. Cloud Function (`api_bq.py`) serve JSON do BigQuery
4. Dashboard HTML (GitHub Pages) faz fetch da Cloud Function
5. Bot Telegram responde perguntas em linguagem natural via Gemini + BigQuery

---

## 2. Arquivos do Projeto

### Pipeline BigQuery (genérico)

| Arquivo | Descrição |
|---------|-----------|
| `omie_sync_bq.py` | Coleta API Omie → BigQuery via MERGE incremental. `ensure_tables()` cria DDL automático |
| `bq_schema.sql` | DDL de referência (9 CREATE TABLE + 1 VIEW) |
| `.github/workflows/sync_omie_bq.yml` | Workflow GitHub Actions — 3x/dia (5h, 12h, 18h BRT) com retry |
| `requirements_bq.txt` | Dependências Python do pipeline |

### API + Dashboard (genérico)

| Arquivo | Descrição |
|---------|-----------|
| `api_bq.py` | Cloud Function — serve HTML na `/` e JSON na `/api/dashboard`. Horários em BRT |
| `main.py` | Entry point para Cloud Function |
| `requirements.txt` | Dependências da Cloud Function |
| `dashboard_bq.html` | Dashboard HTML/Chart.js com 8 abas. GitHub Pages → fetch da Cloud Function |
| `index.html` | Redirect para `dashboard_bq.html` |

### Bot Telegram (genérico)

| Arquivo | Descrição |
|---------|-----------|
| `bot_telegram.py` | Bot que responde perguntas financeiras. Gemini converte NL→SQL, executa no BQ, formata resposta |
| `requirements_bot.txt` | Dependências do bot |

### Koti-Specific (adaptar/remover para outros clientes)

| Arquivo | Descrição |
|---------|-----------|
| `extract_bp_bq.py` | Planilha BP Excel → `orcamento_dre`. DRE_MAP com linhas fixas do Koti |
| `BP.xlsx` | Planilha Business Plan 2026 |

### Legado (será descontinuado)

| Arquivo | Descrição |
|---------|-----------|
| `dashboard_omie.html` | Dashboard original (lê `.enc` criptografados) |
| `omie_sync.py` | Sync v6 → JSON local |
| `extract_orcamento.py` | BP → JSON local |
| `encrypt_data.py` | Criptografa JSON → `.enc` |
| `.github/workflows/sync_omie.yml` | Workflow legado |
| `dados_omie.enc` / `dados_orcamento.enc` | Dados criptografados |

### Removidos

- `dashboard_streamlit.py` e `.streamlit/` — Streamlit descontinuado, substituído pelo dashboard HTML + Cloud Function

---

## 3. Sync Incremental (MERGE)

### Como funciona

O `omie_sync_bq.py` puxa **todos** os dados da API Omie (a API não tem endpoint incremental), mas na hora de escrever no BigQuery usa **MERGE** em vez de TRUNCATE:

1. Dados da API → tabela `_staging` temporária
2. `MERGE` compara staging vs tabela principal pelo `id`
3. **INSERT** registros novos
4. **UPDATE** registros com mudanças (status, valor, categoria, projeto, cliente)
5. **DELETE** registros que sumiram do Omie
6. Limpa staging

### Resultado típico

```
lancamentos: 23 novos, 34 atualizados, 0 removidos, 10.669 iguais
categorias:  0 novos, 0 atualizados, 0 removidos, 142 iguais
projetos:    0 novos, 0 atualizados, 0 removidos, 214 iguais
clientes:    0 novos, 0 atualizados, 0 removidos, 1.837 iguais
```

### Estratégia por tabela

| Tabela | Método | Key | Campos comparados |
|--------|--------|-----|-------------------|
| `lancamentos` | MERGE | `id` | valor, status, data_vencimento, categoria, projeto, cliente |
| `categorias` | MERGE | `codigo` | nome, grupo |
| `projetos` | MERGE | `id` | nome |
| `clientes` | MERGE | `id` | nome_fantasia, razao_social, estado, ativo, pessoa_fisica |
| `saldos_bancarios` | TRUNCATE | — | Snapshot D-1, sempre substitui |
| `vendas_pedidos` | TRUNCATE | — | Explode por item, sem key estável |
| `historico_saldos` | APPEND | — | Acumula, dedup via view |
| `sync_log` | APPEND | — | Log, sempre acumula |

### Frequência

- **3x/dia**: 5h BRT, 12h BRT, 18h BRT (GitHub Actions)
- **Manual**: workflow_dispatch ou `python omie_sync_bq.py` local
- Retry: 2 tentativas, 60s entre elas, timeout 30min

---

## 4. BigQuery — Schema

**Projeto**: `dashboard-koti-omie` | Cada cliente tem seu dataset (ex: `studio_koti`)

### Tabelas

| # | Tabela | Registros (Koti) | Método | Partição |
|:-:|--------|:----------------:|:------:|----------|
| 1 | `lancamentos` | ~10.700 | MERGE | `sync_date` |
| 2 | `saldos_bancarios` | ~17 | TRUNCATE | `sync_date` |
| 3 | `historico_saldos` | acumula | APPEND | `sync_date` |
| 4 | `categorias` | ~142 | MERGE | — |
| 5 | `projetos` | ~214 | MERGE | — |
| 6 | `clientes` | ~1.837 | MERGE | — |
| 7 | `vendas_pedidos` | ~673 | TRUNCATE | `sync_date` |
| 8 | `orcamento_dre` | ~336 | TRUNCATE | — |
| 9 | `sync_log` | acumula | APPEND | — |

### View

| View | Descrição |
|------|-----------|
| `v_historico_saldos` | Dedup de `historico_saldos` por `(conta_id, data_referencia, tipo)` |

### Campos por tabela

**`lancamentos`**: id, tipo, valor, status, data_vencimento, data_emissao, numero_documento, categoria_codigo, categoria_nome, categoria_grupo, projeto_id, projeto_nome, cliente_id, cliente_nome, conta_corrente_id, is_faturamento_direto, sync_timestamp, sync_date

**`saldos_bancarios`**: conta_id, conta_nome, conta_tipo, saldo, saldo_conciliado, diferenca, data_referencia, sync_timestamp, sync_date

**`historico_saldos`**: conta_id, conta_nome, data_referencia, label, saldo_atual, saldo_conciliado, diferenca, tipo, sync_timestamp, sync_date

**`categorias`**: codigo, nome, grupo, sync_timestamp

**`projetos`**: id, nome, sync_timestamp

**`clientes`**: id, nome_fantasia, razao_social, estado, ativo, pessoa_fisica, data_cadastro, sync_timestamp

**`vendas_pedidos`**: pedido_id, valor_mercadorias, etapa, data_previsao, produto_descricao, produto_quantidade, produto_valor_total, sync_timestamp, sync_date

**`orcamento_dre`**: label, section, level, mes, valor_real, valor_bp, variacao_pct, mes_com_real, sync_timestamp

**`sync_log`**: sync_id, started_at, finished_at, status, duration_seconds, lancamentos_count, saldos_count, clientes_count, projetos_count, categorias_count, error_message, is_incremental

---

## 5. Dashboard — 8 Abas

| # | Aba | Componentes | Genérica? |
|:-:|-----|-------------|:---------:|
| 1 | **Visão Geral** | KPIs (saldo D-1, entradas, saídas, previsão), saldo por conta, fluxo mensal, saldo acumulado, top categorias desp/rec | Sim |
| 2 | **Fluxo de Caixa** | KPIs com breakdown realizado/a realizar, despesas e receitas por grupo (barras), custos Direto/SG&A/Outros stacked (realizado vs a realizar com valores), receitas stacked, tabela consolidada (Realizado \| A Realizar \| Total) ou mês a mês | Sim |
| 3 | **Financeiro** | Receita vs custo vs SG&A, resultado mensal, composição saídas, detalhamento mês a mês, contas a receber/pagar | Sim |
| 4 | **Conciliação** | Cards por conta (OK/Atenção/Pendente), evolução mensal/diário, filtro por banco | Sim |
| 5 | **Vendas** | Total, pedidos, ticket médio, por mês, por etapa, top clientes/projetos | Sim |
| 6 | **Clientes** | Total, ativos, PF/PJ, por estado, novos por mês | Sim |
| 7 | **Projetos** | Top projetos receita/despesa, busca, tabela detalhada | Sim |
| 8 | **Real vs Orçado** | Timeline com régua (Jan-Dez), KPIs, gráficos receita/EBITDA filtrados pela régua, waterfall, % atingimento, tabela DRE. Modos: acumulado ou mês a mês | **Koti-specific** |

### Fluxo de Caixa — detalhe

- **KPIs**: Total Receitas (Recebido X% · A receber), Total Despesas (Pago X% · A pagar), Resultado (Realizado · A realizar), Margem
- **Gráficos por grupo**: barras horizontais simples (top 10)
- **Gráficos stacked**: Custos (Direto/SG&A/Outros) e Receitas com breakdown Pago vs A Pagar / Recebido vs A Receber, com valores nas barras
- **Tabela consolidada**: Categoria | Realizado (net) | A Realizar (net) | Total
- **Tabela mês a mês**: Categoria × Mês com resultado net

---

## 6. API Cloud Function (`api_bq.py`)

### URLs

| Ambiente | URL |
|----------|-----|
| Produção | https://us-central1-dashboard-koti-omie.cloudfunctions.net/api_dashboard |
| Local | http://localhost:8080 |

### Rotas

| Rota | Local | Produção |
|------|-------|----------|
| `GET /` | `dashboard_bq.html` | N/A (HTML via GitHub Pages) |
| `GET /api/dashboard` | JSON do BigQuery | JSON do BigQuery |

### Rodar localmente

```bash
GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-key.json \
GCP_PROJECT_ID=dashboard-koti-omie \
python3 api_bq.py
```

### Deploy

```bash
gcloud functions deploy api_dashboard \
  --gen2 --runtime python311 --trigger-http --allow-unauthenticated \
  --entry-point api_dashboard --source . \
  --set-env-vars GCP_PROJECT_ID=dashboard-koti-omie,BQ_DATASET=studio_koti \
  --region us-central1 --memory 512MB --timeout 60s
```

**Notas**: 512MB (256 não basta), billing obrigatório, horários em BRT, cache 5min.

---

## 7. Bot Telegram (`bot_telegram.py`)

### @Kotifin_bot

Responde perguntas financeiras em linguagem natural:
- "Quanto faturamos em fevereiro?"
- "Qual o saldo do BTG?"
- "Contas a pagar vencidas"
- "Top 5 clientes por receita"

### Como funciona

1. Usuário manda mensagem no Telegram
2. Gemini (`gemini-2.5-flash` via `google-genai` SDK) converte pergunta → SQL BigQuery
3. SQL validado (só SELECT, só dataset autorizado)
4. Executa no BigQuery (timeout 15s, limit 20 rows)
5. Gemini formata resultado em resposta amigável (R$ brasileiro, emojis)

### Comandos

| Comando | Ação |
|---------|------|
| `/start` | Menu com exemplos |
| `/saldo` | Saldos bancários (query direta) |
| `/status` | Último sync |
| `/ajuda` | Mais exemplos |

### Rodar localmente

```bash
TELEGRAM_BOT_TOKEN=<token> \
GEMINI_API_KEY=<key> \
GCP_PROJECT_ID=dashboard-koti-omie \
GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-key.json \
python3 bot_telegram.py --local
```

### API Key Gemini

A API key é do projeto GCP com billing (`AIzaSyB1iNj...`). Criada via:
```bash
gcloud services api-keys create --display-name="gemini-bot" --project=dashboard-koti-omie
```

Usa modelo `gemini-2.5-flash` (mais recente). O `gemini-2.0-flash` não está mais disponível para novos projetos.

### Segurança

- `AUTHORIZED_CHAT_IDS`: limitar quem pode usar (opcional, comma-separated)
- SQL validado: só SELECT, só o dataset configurado em `BQ_DATASET`, palavras perigosas bloqueadas
- Respostas truncadas em 4000 chars (limite Telegram)

---

## 8. Lógica Koti-Specific

Marcadas com `# ⚡ KOTI-SPECIFIC` no código.

| Item | Arquivo | Para novo cliente |
|------|---------|-------------------|
| `CONTAS_IGNORAR = {8754849088}` | `omie_sync_bq.py` | Adaptar ou esvaziar |
| `is_faturamento_direto` | `omie_sync_bq.py` | Remover ou adaptar |
| `DRE_MAP` (linhas da planilha) | `extract_bp_bq.py` | Ajustar para planilha do cliente |
| `MONTH_COLS` (ano 2026) | `extract_bp_bq.py` | Ajustar ano |
| `BP.xlsx` | raiz do repo | Substituir |
| `PASS_HASH` | `dashboard_bq.html` | `echo -n "senha" \| shasum -a 256` |
| Aba "Real vs Orçado" | `dashboard_bq.html` | Funciona se `orcamento_dre` tiver dados; senão fallback |

**Status dos lançamentos** (Omie, genérico): PAGO, RECEBIDO, A VENCER, ATRASADO, VENCE HOJE, CANCELADO

---

## 9. Novo Cliente Omie — Checklist

### BigQuery
```bash
bq mk --dataset --location=US dashboard-koti-omie:nome_cliente
# Tabelas criadas automaticamente pelo ensure_tables() no primeiro sync
```

### Repositório
- [ ] Fork ou copiar repo
- [ ] GitHub Secrets: `OMIE_APP_KEY`, `OMIE_APP_SECRET`, `GCP_PROJECT_ID`, `GCP_SA_KEY`
- [ ] Alterar `BQ_DATASET` no workflow
- [ ] Buscar `⚡ KOTI-SPECIFIC` e adaptar (ver tabela seção 8)

### Orçamento (opcional)
- [ ] Com BP: adaptar `DRE_MAP` + `BP.xlsx`
- [ ] Sem BP: remover `extract_bp_bq.py` do workflow (aba mostra fallback)

### API + Dashboard
- [ ] Deploy Cloud Function com `BQ_DATASET=nome_cliente`
- [ ] Copiar `dashboard_bq.html`, atualizar `PASS_HASH`
- [ ] GitHub Pages no novo repo
- [ ] Permissão BQ: `gcloud projects add-iam-policy-binding ... --role=roles/bigquery.dataViewer`

### Bot Telegram
- [ ] Criar bot no @BotFather
- [ ] Deploy com `BQ_DATASET=nome_cliente` e novo token
- [ ] Configurar `AUTHORIZED_CHAT_IDS` se necessário

### Estrutura multi-cliente
```
Projeto GCP: dashboard-koti-omie
├── Dataset: studio_koti     ← Studio Koti
├── Dataset: cliente_2       ← Segundo cliente
└── Dataset: cliente_3       ← Terceiro cliente
```

---

## 10. GitHub Secrets

| Secret | Valor | Usado por |
|--------|-------|-----------|
| `OMIE_APP_KEY` | Chave API Omie | `omie_sync_bq.py` |
| `OMIE_APP_SECRET` | Secret API Omie | `omie_sync_bq.py` |
| `GCP_PROJECT_ID` | `dashboard-koti-omie` | todos scripts BQ |
| `GCP_SA_KEY` | JSON service account (base64) | workflow |
| `DASHBOARD_PASSWORD` | Senha dashboard legado (será descontinuado) | `encrypt_data.py` |

---

## 11. Variáveis de Ambiente

| Variável | Usado por | Obrigatória |
|----------|-----------|:-----------:|
| `OMIE_APP_KEY` | sync | Sim |
| `OMIE_APP_SECRET` | sync | Sim |
| `GCP_PROJECT_ID` | todos | Sim |
| `BQ_DATASET` | todos | Não (default: `studio_koti`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | local | Sim (local) |
| `TELEGRAM_BOT_TOKEN` | bot | Sim (bot) |
| `GEMINI_API_KEY` | bot | Sim (bot) |
| `AUTHORIZED_CHAT_IDS` | bot | Não |
| `API_CORS_ORIGIN` | api | Não (default: `*`) |

---

## 12. Troubleshooting

| Problema | Causa | Solução |
|----------|-------|---------|
| Cloud Function 503 | Memória | Usar `--memory 512MB` |
| Cloud Function 403 | SA sem permissão | `bigquery.dataViewer` + `bigquery.jobUser` |
| "Table not found" | Tabelas/view não criadas | Rodar `omie_sync_bq.py` (ensure_tables) |
| Dashboard "Erro ao conectar" | API down | Verificar URL Cloud Function |
| Horário errado | Timezone | `api_bq.py` usa BRT (UTC-3) |
| Aba Orçamento vazia | Sem dados BP | Rodar `extract_bp_bq.py` com `BP.xlsx` |
| Dados desatualizados | Sync não rodou | Sync 3x/dia. Rodar manual: `python omie_sync_bq.py` |
| MERGE falha | Staging table | Limpar manualmente: `bq rm studio_koti.lancamentos_staging` |
| Bot 429 quota | API key free tier | Usar key do projeto com billing |
| Bot 404 model | Modelo indisponível | Usar `gemini-2.5-flash` |
| Vertex AI 404 | Termos não aceitos | Aceitar em console.cloud.google.com/vertex-ai |
| Deploy falha "billing" | Sem billing | Habilitar em console.cloud.google.com/billing |
| Deploy falha "main.py" | Faltando | `main.py` importa de `api_bq.py` |

---

## 13. Custos

| Item | Custo mensal |
|------|:------------:|
| BigQuery storage | R$ 0 (free tier) |
| BigQuery queries | R$ 0 (free tier) |
| Cloud Functions | R$ 0 (free tier) |
| GitHub Actions (3x/dia) | R$ 0 (free tier) |
| GitHub Pages | R$ 0 |
| Gemini API (bot) | ~R$ 0-5 |
| **Budget configurado** | **US$ 4/mês (~R$ 20)** |
| **Total estimado** | **~R$ 0-5/mês** |

Alertas de billing em 50%, 80% e 100% do budget.

---

## 14. Próximos Passos

- [x] Pipeline BigQuery com MERGE incremental
- [x] Dashboard HTML (8 abas) em produção (GitHub Pages)
- [x] API Cloud Function em produção
- [x] Bot Telegram (@Kotifin_bot)
- [x] Sync 3x/dia (5h, 12h, 18h BRT)
- [x] Budget GCP (US$ 4/mês)
- [x] Fluxo de Caixa com Realizado vs A Realizar
- [ ] Descontinuar pipeline legado
- [ ] Onboarding segundo cliente Omie
- [ ] Bot WhatsApp (mesmo fluxo do Telegram, via WhatsApp Business API)
- [ ] Agente com memória de contexto (histórico de conversa)
- [ ] Deploy do bot em produção (Cloud Run ou VM)
