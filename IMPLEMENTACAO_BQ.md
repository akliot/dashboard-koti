# Dashboard Financeiro — Omie + BigQuery

**Projeto GCP**: `dashboard-koti-omie`
**Repositório**: https://github.com/akliot/dashboard-koti
**Dashboard (produção)**: https://akliot.github.io/dashboard-koti/
**API (produção)**: https://us-central1-dashboard-koti-omie.cloudfunctions.net/api_dashboard
**Última atualização**: 20/03/2026

> Sistema para empresas que usam Omie ERP. O primeiro cliente é o **Studio Koti** (dataset `studio_koti`). Para adicionar novos clientes, ver seção 9.

---

## 1. Arquitetura

```
┌──────────┐     ┌─────────────────┐     ┌──────────────┐
│ API Omie │────▶│ omie_sync_bq.py │────▶│   BigQuery    │
│ (ERP)    │     │ (GitHub Actions) │     │   (GCP)      │
└──────────┘     └─────────────────┘     └──────┬───────┘
                                                │
                  ┌─────────────────┐           │
                  │ extract_bp_bq.py│──────────▶│  (⚡ Koti-specific)
                  │ (BP → BigQuery)  │           │
                  └─────────────────┘           │
                                                │
                                         ┌──────┴───────┐
                                         │  api_bq.py    │
                                         │ Cloud Function │
                                         │ (serve JSON)  │
                                         └──────┬───────┘
                                                │ fetch
                                         ┌──────┴───────┐
                                         │dashboard_bq   │
                                         │.html          │
                                         │(GitHub Pages) │
                                         └──────────────┘
```

**Fluxo diário:**
1. **GitHub Actions** roda às 5h BRT (8h UTC) com retry automático
2. `omie_sync_bq.py` coleta toda a API Omie → BigQuery (9 tabelas)
3. `extract_bp_bq.py` extrai planilha BP → tabela `orcamento_dre` (Koti-specific)
4. **Cloud Function** (`api_bq.py`) consulta BigQuery e serve JSON
5. **Dashboard** (`dashboard_bq.html`) hospedado no GitHub Pages faz fetch da Cloud Function e renderiza com Chart.js

**URLs em produção:**
- Dashboard: https://akliot.github.io/dashboard-koti/ (redireciona para `dashboard_bq.html`)
- API JSON: https://us-central1-dashboard-koti-omie.cloudfunctions.net/api_dashboard
- Horários mostrados em BRT (UTC-3)

---

## 2. Arquivos do Projeto

### Pipeline BigQuery (genérico — qualquer cliente Omie)

| Arquivo | Descrição |
|---------|-----------|
| `omie_sync_bq.py` | Coleta API Omie → BigQuery. Cria tabelas automaticamente via `ensure_tables()` |
| `bq_schema.sql` | DDL de referência (9 CREATE TABLE + 1 VIEW) |
| `.github/workflows/sync_omie_bq.yml` | Workflow GitHub Actions (BQ + legacy em paralelo) |
| `requirements_bq.txt` | Dependências Python (sync + streamlit + plotly) |

### API + Dashboard (genérico)

| Arquivo | Descrição |
|---------|-----------|
| `api_bq.py` | Cloud Function — consulta BigQuery e serve JSON. Localmente: serve HTML na `/` e JSON na `/api/dashboard` |
| `main.py` | Entry point para Cloud Function (importa `api_dashboard` de `api_bq.py`) |
| `requirements.txt` | Dependências da Cloud Function (google-cloud-bigquery, db-dtypes, functions-framework) |
| `dashboard_bq.html` | Dashboard HTML/Chart.js com 8 abas. Hospedado no GitHub Pages, faz fetch da Cloud Function |
| `index.html` | Redirect para `dashboard_bq.html` |

### Koti-Specific (adaptar/remover para outros clientes)

| Arquivo | Descrição |
|---------|-----------|
| `extract_bp_bq.py` | Extrai planilha BP Excel → `orcamento_dre`. DRE_MAP com linhas fixas da planilha Koti |
| `BP.xlsx` | Planilha Business Plan 2026 do Studio Koti |

### Dashboard legado (GitHub Pages — será descontinuado)

| Arquivo | Descrição |
|---------|-----------|
| `dashboard_omie.html` | Dashboard original (lê `.enc` criptografados) |
| `omie_sync.py` | Sync v6 → JSON local |
| `extract_orcamento.py` | BP → JSON local |
| `encrypt_data.py` | Criptografa JSON → `.enc` |
| `.github/workflows/sync_omie.yml` | Workflow legado |

### Experimental

| Arquivo | Descrição |
|---------|-----------|
| `dashboard_streamlit.py` | Dashboard Streamlit (7 páginas) — funcional mas visual inferior ao HTML |
| `.streamlit/config.toml` | Tema escuro Streamlit |

### Infra

| Arquivo | Descrição |
|---------|-----------|
| `setup_gcp.sh` | Script de setup GCP (criar projeto, SA, dataset) |
| `.gitignore` | Exclui secrets, JSON em texto plano, `.streamlit/secrets.toml` |
| `dados_omie.enc` / `dados_orcamento.enc` | Dados criptografados (legado) |

---

## 3. BigQuery — Schema

**Projeto**: `dashboard-koti-omie`

Cada cliente tem seu próprio dataset (ex: `studio_koti`) com as mesmas tabelas.

### Tabelas

| # | Tabela | Registros (Koti) | Estratégia | Partição | Cluster |
|:-:|--------|:----------------:|:----------:|----------|---------|
| 1 | `lancamentos` | ~10.700 | WRITE_TRUNCATE | `sync_date` | `tipo`, `categoria_grupo` |
| 2 | `saldos_bancarios` | ~17 | WRITE_TRUNCATE | `sync_date` | — |
| 3 | `historico_saldos` | acumula | WRITE_APPEND | `sync_date` | `conta_id`, `tipo` |
| 4 | `categorias` | ~142 | WRITE_TRUNCATE | — | — |
| 5 | `projetos` | ~214 | WRITE_TRUNCATE | — | — |
| 6 | `clientes` | ~1.837 | WRITE_TRUNCATE | — | — |
| 7 | `vendas_pedidos` | variável | WRITE_TRUNCATE | `sync_date` | — |
| 8 | `orcamento_dre` | ~336 | WRITE_TRUNCATE | — | — |
| 9 | `sync_log` | acumula | WRITE_APPEND | — | — |

### View

| View | Descrição |
|------|-----------|
| `v_historico_saldos` | Dedup de `historico_saldos` por `(conta_id, data_referencia, tipo)` — mantém último sync |

### Campos por tabela

**`lancamentos`**: id, tipo, valor, status, data_vencimento, data_emissao, numero_documento, categoria_codigo, categoria_nome, categoria_grupo, projeto_id, projeto_nome, cliente_id, cliente_nome, conta_corrente_id, is_faturamento_direto, sync_timestamp, sync_date

**`saldos_bancarios`**: conta_id, conta_nome, conta_tipo, saldo, saldo_conciliado, diferenca, data_referencia, sync_timestamp, sync_date

**`historico_saldos`**: conta_id, conta_nome, data_referencia, label, saldo_atual, saldo_conciliado, diferenca, tipo (mensal/diario), sync_timestamp, sync_date

**`categorias`**: codigo, nome, grupo, sync_timestamp

**`projetos`**: id, nome, sync_timestamp

**`clientes`**: id, nome_fantasia, razao_social, estado, ativo, pessoa_fisica, data_cadastro, sync_timestamp

**`vendas_pedidos`**: pedido_id, valor_mercadorias, etapa, data_previsao, produto_descricao, produto_quantidade, produto_valor_total, sync_timestamp, sync_date

**`orcamento_dre`**: label, section, level, mes, valor_real, valor_bp, variacao_pct, mes_com_real, sync_timestamp

**`sync_log`**: sync_id, started_at, finished_at, status, duration_seconds, lancamentos_count, saldos_count, clientes_count, projetos_count, categorias_count, error_message, is_incremental

---

## 4. Dashboard — 8 Abas

| # | Aba | Componentes | Genérica? |
|:-:|-----|-------------|:---------:|
| 1 | **Visão Geral** | KPIs (saldo D-1, entradas, saídas, previsão), saldo por conta (grid), fluxo mensal (barras), saldo acumulado (linha), top categorias desp/rec (donuts) | Sim |
| 2 | **Fluxo de Caixa** | KPIs, despesas/receitas por grupo (barras), pivot categoria × mês (consolidado ou mês a mês) | Sim |
| 3 | **Financeiro** | KPIs (receita, despesa, resultado, top grupos, a receber/pagar), receita vs custo vs SG&A (barras empilhadas), resultado mensal, composição de saídas, detalhamento mês a mês, contas a receber vs pagar | Sim |
| 4 | **Conciliação** | Cards por conta (OK/Atenção/Pendente), evolução conciliação (mensal/diário), filtro por banco | Sim |
| 5 | **Vendas** | KPIs (total, pedidos, ticket médio), vendas por mês, por etapa, top clientes, top projetos | Sim |
| 6 | **Clientes** | KPIs (total, ativos, PF/PJ), novos por mês, por estado, tipo pessoa, status | Sim |
| 7 | **Projetos** | KPIs, top projetos receita/despesa, busca, tabela detalhada | Sim |
| 8 | **Real vs Orçado** | Timeline com régua (12 meses), KPIs (receita/EBITDA/LL real vs BP), gráficos receita e EBITDA (filtrados pela régua), waterfall variação, % atingimento, tabela DRE comparativa. Modos: acumulado ou mês a mês | **Koti-specific** |

### Aba Real vs Orçado — detalhes

Depende da tabela `orcamento_dre` (alimentada por `extract_bp_bq.py` + `BP.xlsx`).

- **Régua**: timeline clicável Jan-Dez. Meses com dados reais em verde. Todos os componentes (KPIs, gráficos, waterfall, tabela) respondem ao mês selecionado.
- **Para novo cliente com BP**: adaptar `DRE_MAP` no `extract_bp_bq.py`
- **Para novo cliente sem BP**: aba mostra "Dados de orçamento não disponíveis" (fallback automático)
- A API só inclui `orcamento` no JSON se a tabela tiver dados

---

## 5. API Cloud Function (`api_bq.py`)

### URLs

| Ambiente | URL |
|----------|-----|
| **Produção** | https://us-central1-dashboard-koti-omie.cloudfunctions.net/api_dashboard |
| **Local** | http://localhost:8080 |

### Rotas (servidor local)

| Rota | Resposta |
|------|----------|
| `GET /` | `dashboard_bq.html` (text/html) |
| `GET /api/dashboard` | JSON com dados do BigQuery |
| `GET /api` | Alias para `/api/dashboard` |

### Rodar localmente

```bash
GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-key.json \
GCP_PROJECT_ID=dashboard-koti-omie \
BQ_DATASET=studio_koti \
python3 api_bq.py
# Acesse http://localhost:8080
```

### Deploy (Cloud Function Gen2)

```bash
cd ~/dashboard-koti
gcloud functions deploy api_dashboard \
  --gen2 \
  --runtime python311 \
  --trigger-http \
  --allow-unauthenticated \
  --entry-point api_dashboard \
  --source . \
  --set-env-vars GCP_PROJECT_ID=dashboard-koti-omie,BQ_DATASET=studio_koti \
  --region us-central1 \
  --memory 512MB \
  --timeout 60s \
  --project dashboard-koti-omie
```

**Notas de deploy:**
- Precisa de billing habilitado no projeto GCP (free tier, sem cobrança)
- 512MB de memória (256MB não é suficiente para ~10K lançamentos)
- APIs necessárias: `cloudfunctions.googleapis.com`, `cloudbuild.googleapis.com`, `run.googleapis.com`, `artifactregistry.googleapis.com`
- Service account padrão do Compute Engine precisa de roles `bigquery.dataViewer` + `bigquery.jobUser`
- Horários mostrados em BRT (UTC-3)

### JSON retornado

```json
{
  "atualizado_em": "ISO datetime",
  "atualizado_em_formatado": "DD/MM/YYYY às HH:MM (BRT)",
  "lancamentos": [{"id", "valor", "tipo", "status", "data" (DD/MM/YYYY), "categoria", "categoria_nome", "projeto", "projeto_nome", "cliente_nome"}],
  "saldos_bancarios": [{"id", "nome", "tipo", "saldo", "saldo_conciliado", "diferenca", "data" (DD/MM/YYYY)}],
  "historico_conciliacao": [{"banco_id", "banco_nome", "data" (YYYY-MM-DD), "label", "saldo_atual", "saldo_conciliado", "diferenca", "tipo"}],
  "categorias": {"codigo": "nome"},
  "projetos": [{"id", "nome"}],
  "vendas": {"total_vendas", "quantidade_pedidos", "ticket_medio", "por_mes", "por_etapa", "top_produtos"},
  "clientes": {"total_clientes", "ativos", "inativos", "pessoa_fisica", "pessoa_juridica", "por_estado", "por_mes_cadastro"},
  "orcamento": {"meses_disponiveis", "meses_com_real", "dre": [{"label", "section", "level", "bp": {}, "real": {}}]}
}
```

O campo `orcamento` só aparece se `orcamento_dre` tiver dados.

### Cache

A Cloud Function retorna header `Cache-Control: public, max-age=300` (5 minutos). O dashboard mostra dados atualizados a cada refresh após 5 min.

---

## 6. Pipeline de Sync

### `omie_sync_bq.py`

1. Valida credenciais (`OMIE_APP_KEY/SECRET`, `GCP_PROJECT_ID`)
2. `ensure_tables()` — cria dataset, 9 tabelas e view `v_historico_saldos` via DDL
3. Registra sync no `sync_log` (status=running)
4. Coleta API Omie:
   - Categorias (`ListarCategorias` + `ConsultarCategoria` para faltantes)
   - Projetos (`ListarProjetos`)
   - Saldos bancários (`ListarExtrato` — snapshot D-1 + histórico mensal/diário com cache BQ)
   - Clientes (`ListarClientes` — bulk)
   - Lançamentos (`ListarContasReceber` + `ListarContasPagar`)
   - Vendas (`ListarPedidos` — 1 linha por item)
5. Carrega no BigQuery (TRUNCATE ou APPEND)
6. Registra sucesso/falha no `sync_log`

### GitHub Actions (`sync_omie_bq.yml`)

- **Cron**: `0 8 * * *` (5h BRT / 8h UTC)
- **Retry**: 2 tentativas, 60s entre elas, timeout 30min
- **Etapas**: Install deps → Auth GCP → Sync BQ (com retry) → Extract BP → Cleanup → Legacy pipeline
- Pipeline legado roda em paralelo (commit/push dos `.enc`)

### Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|----------|:-----------:|-----------|
| `OMIE_APP_KEY` | Sim | Chave da API Omie |
| `OMIE_APP_SECRET` | Sim | Secret da API Omie |
| `GCP_PROJECT_ID` | Sim | Projeto GCP (ex: `dashboard-koti-omie`) |
| `BQ_DATASET` | Não | Dataset BigQuery (default: `studio_koti`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Sim (local) | Path para JSON da service account |
| `API_CORS_ORIGIN` | Não | Origin CORS (default: `*`) |

---

## 7. Lógica Koti-Specific

Todas marcadas com `# ⚡ KOTI-SPECIFIC` no código. Buscar e adaptar para cada novo cliente.

| Item | Arquivo | O que fazer para novo cliente |
|------|---------|-------------------------------|
| `CONTAS_IGNORAR = {8754849088}` | `omie_sync_bq.py` | Substituir por contas fictícias do cliente (ou `set()` vazio) |
| `is_faturamento_direto` | `omie_sync_bq.py` | Remover ou adaptar lógica de FD |
| `DRE_MAP` (linhas fixas da planilha) | `extract_bp_bq.py` | Ajustar números de linha para planilha BP do cliente |
| `MONTH_COLS` (ano 2026) | `extract_bp_bq.py` | Ajustar ano e colunas |
| `BP.xlsx` | raiz do repo | Substituir pela planilha do cliente |
| `PASS_HASH` (senha do dashboard) | `dashboard_bq.html` | Gerar novo hash: `echo -n "nova_senha" \| shasum -a 256` |
| Aba "Real vs Orçado" | `dashboard_bq.html` | Funciona se `orcamento_dre` tiver dados; senão mostra fallback |

**Status dos lançamentos** (Omie, genérico):
- `PAGO` (~7.926) — saída liquidada
- `RECEBIDO` (~1.075) — entrada liquidada
- `A VENCER` (~1.229) — pendente, dentro do prazo
- `ATRASADO` (~340) — vencido
- `VENCE HOJE` (~91) — vence no dia
- `CANCELADO` (~40) — cancelado

---

## 8. GitHub Secrets

| Secret | Valor | Usado por |
|--------|-------|-----------|
| `OMIE_APP_KEY` | Chave API Omie do cliente | `omie_sync_bq.py` |
| `OMIE_APP_SECRET` | Secret API Omie do cliente | `omie_sync_bq.py` |
| `GCP_PROJECT_ID` | `dashboard-koti-omie` | todos os scripts BQ |
| `GCP_SA_KEY` | JSON service account (base64) | workflow |
| `DASHBOARD_PASSWORD` | Senha do dashboard legado | `encrypt_data.py` |

Encodar a chave:
```bash
cat gcp-key.json | base64 | pbcopy
```

---

## 9. Novo Cliente Omie — Checklist

### Passo 1: BigQuery

```bash
bq mk --dataset --location=US dashboard-koti-omie:nome_cliente
```

As tabelas e a view são criadas automaticamente no primeiro sync (`ensure_tables()`).

### Passo 2: Repositório

- [ ] Fork ou copiar o repo
- [ ] Configurar GitHub Secrets:
  - `OMIE_APP_KEY` / `OMIE_APP_SECRET` do novo cliente
  - `GCP_PROJECT_ID` = `dashboard-koti-omie`
  - `GCP_SA_KEY` = JSON service account (base64)
- [ ] Alterar `BQ_DATASET` no workflow para `nome_cliente`
- [ ] Buscar `⚡ KOTI-SPECIFIC` e adaptar/remover (ver tabela na seção 7)

### Passo 3: Orçamento (opcional — Koti-specific)

Se o cliente tem planilha Business Plan:
- [ ] Adaptar `DRE_MAP` no `extract_bp_bq.py` com linhas da planilha do cliente
- [ ] Colocar `BP.xlsx` na raiz do repo

Se não tem:
- [ ] Remover `extract_bp_bq.py` do workflow — a aba "Real vs Orçado" mostra fallback

### Passo 4: API + Dashboard

- [ ] Deploy Cloud Function com `BQ_DATASET=nome_cliente`:
  ```bash
  gcloud functions deploy api_nome_cliente \
    --gen2 --runtime python311 --trigger-http --allow-unauthenticated \
    --entry-point api_dashboard --source . \
    --set-env-vars GCP_PROJECT_ID=dashboard-koti-omie,BQ_DATASET=nome_cliente \
    --region us-central1 --memory 512MB --timeout 60s
  ```
- [ ] Copiar `dashboard_bq.html`, atualizar:
  - `PASS_HASH` com senha do cliente
  - `DASHBOARD_API_URL` se usar função separada
- [ ] Hospedar no GitHub Pages do novo repo
- [ ] Dar permissão BigQuery à service account do Compute Engine:
  ```bash
  gcloud projects add-iam-policy-binding dashboard-koti-omie \
    --member="serviceAccount:294770561801-compute@developer.gserviceaccount.com" \
    --role="roles/bigquery.dataViewer"
  ```

### Estrutura multi-cliente

```
Projeto GCP: dashboard-koti-omie
├── Dataset: studio_koti        ← Studio Koti
├── Dataset: cliente_2          ← Segundo cliente
├── Dataset: cliente_3          ← Terceiro cliente
└── Dataset: consolidado        ← Views cross-dataset (opcional)

Cloud Functions:
├── api_dashboard               ← Studio Koti (BQ_DATASET=studio_koti)
├── api_cliente_2               ← Segundo cliente (BQ_DATASET=cliente_2)
└── api_cliente_3               ← Terceiro cliente

GitHub Pages (um repo por cliente):
├── akliot.github.io/dashboard-koti/
├── akliot.github.io/dashboard-cliente2/
└── akliot.github.io/dashboard-cliente3/
```

### Visão consolidada (opcional)

```sql
CREATE OR REPLACE VIEW `dashboard-koti-omie.consolidado.lancamentos_all` AS
SELECT 'Studio Koti' AS cliente, * FROM `studio_koti.lancamentos`
UNION ALL
SELECT 'Cliente 2' AS cliente, * FROM `cliente_2.lancamentos`;
```

---

## 10. Troubleshooting

| Problema | Causa | Solução |
|----------|-------|---------|
| Cloud Function 503 | Memória insuficiente | Usar `--memory 512MB` (256MB não basta para ~10K registros) |
| Cloud Function 403 | SA sem permissão BQ | Adicionar `bigquery.dataViewer` + `bigquery.jobUser` à SA do Compute Engine |
| "Table not found" | View/tabelas não criadas | Rodar `omie_sync_bq.py` (cria via `ensure_tables()`) ou criar view manualmente |
| Dashboard "Erro ao conectar" | API down ou CORS | Verificar URL da Cloud Function e se está respondendo |
| Horário errado no dashboard | Timezone UTC | Verificar que `api_bq.py` usa `BRT = timezone(timedelta(hours=-3))` |
| Aba Orçamento vazia | `orcamento_dre` sem dados | Rodar `extract_bp_bq.py` com `BP.xlsx` no repo |
| Régua não muda gráficos | Bug antigo (corrigido) | Atualizar `dashboard_bq.html` para versão mais recente |
| Sync falhou | API Omie fora do ar | Verificar credenciais. Workflow tem retry automático (2x) |
| Histórico duplicado | WRITE_APPEND | Usar view `v_historico_saldos` (dedup automático, já é o padrão) |
| Deploy falha "billing" | Billing não habilitado | Habilitar em https://console.cloud.google.com/billing |
| Deploy falha "API disabled" | Cloud Functions API off | `gcloud services enable cloudfunctions.googleapis.com cloudbuild.googleapis.com run.googleapis.com artifactregistry.googleapis.com` |
| Deploy falha "main.py not found" | Arquivo faltando | `main.py` deve existir na raiz (importa `api_dashboard` de `api_bq.py`) |
| Deploy falha "requirements.txt" | Arquivo faltando | `requirements.txt` (não `requirements_bq.txt`) com: google-cloud-bigquery, db-dtypes, functions-framework |

---

## 11. Custos

| Item | Custo mensal |
|------|:------------:|
| BigQuery storage (por cliente) | R$ 0 (free tier < 10 GB) |
| BigQuery queries | R$ 0 (free tier: 1 TB/mês) |
| Cloud Functions | R$ 0 (free tier: 2M invocações/mês) |
| GitHub Actions | R$ 0 (free tier: 2000 min/mês) |
| GitHub Pages | R$ 0 |
| **Total por cliente** | **R$ 0/mês** |

Com 10+ clientes pesados, pode sair do free tier (~R$ 5-20/mês por cliente adicional).

---

## 12. Próximos Passos

- [x] Pipeline BigQuery (`omie_sync_bq.py`)
- [x] Dashboard HTML com Chart.js (`dashboard_bq.html`)
- [x] API Cloud Function (`api_bq.py`) — produção
- [x] GitHub Pages — produção
- [x] Documentação completa
- [ ] Descontinuar pipeline legado (GitHub Pages `.enc`)
- [ ] Onboarding segundo cliente Omie
- [ ] Fase 2: Agente WhatsApp (Claude API + WhatsApp Business API + BigQuery)
