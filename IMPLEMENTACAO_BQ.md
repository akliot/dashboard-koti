# Dashboard Financeiro — Omie + BigQuery

**Projeto GCP**: `dashboard-koti-omie`
**Repositório**: https://github.com/akliot/dashboard-koti
**Última atualização**: 20/03/2026

> Sistema genérico para qualquer empresa que usa Omie ERP. O primeiro cliente é o **Studio Koti** (dataset `studio_koti`). Para adicionar novos clientes, ver seção 8.

---

## 1. Arquitetura

```
┌──────────┐     ┌─────────────────┐     ┌─────────────┐     ┌────────────────┐
│ API Omie │────▶│ omie_sync_bq.py │────▶│  BigQuery    │────▶│ dashboard_bq   │
│ (ERP)    │     │ (GitHub Actions) │     │  (GCP)      │     │  .html         │
└──────────┘     └─────────────────┘     └──────┬──────┘     └────────────────┘
                                                │                    ▲
                  ┌─────────────────┐           │                    │ fetch JSON
                  │ extract_bp_bq.py│──────────▶│              ┌─────┴──────┐
                  │ (BP → BigQuery)  │           │              │ api_bq.py  │
                  └─────────────────┘           │              │ (Cloud Func│
                                                │              │  ou local) │
                                                └──────────────┘────────────┘
```

**Fluxo diário:**
1. GitHub Actions roda às **5h BRT** (8h UTC)
2. `omie_sync_bq.py` coleta API Omie → BigQuery (9 tabelas)
3. `extract_bp_bq.py` extrai planilha BP → tabela `orcamento_dre` (Koti-specific)
4. `api_bq.py` consulta BigQuery e serve JSON via HTTP
5. `dashboard_bq.html` faz fetch da API e renderiza (Chart.js)

**Rotas da API (`api_bq.py`):**
- `GET /` → serve `dashboard_bq.html`
- `GET /api/dashboard` → serve JSON com todos os dados do BigQuery

---

## 2. Arquivos do Projeto

### Pipeline BigQuery (genérico — funciona para qualquer cliente Omie)

| Arquivo | Descrição |
|---------|-----------|
| `omie_sync_bq.py` | Coleta API Omie → BigQuery. Inclui `ensure_tables()` (DDL automático) |
| `bq_schema.sql` | DDL de referência (9 CREATE TABLE + 1 VIEW) |
| `.github/workflows/sync_omie_bq.yml` | Workflow GitHub Actions (BQ + legacy em paralelo) |
| `requirements_bq.txt` | Dependências Python |

### Dashboard BigQuery (genérico)

| Arquivo | Descrição |
|---------|-----------|
| `api_bq.py` | API HTTP — serve HTML na `/` e JSON na `/api/dashboard`. Roda como Cloud Function ou local |
| `dashboard_bq.html` | Dashboard HTML/Chart.js com 8 abas. Lê dados via fetch da API |

### Koti-Specific (adaptar/remover para outros clientes)

| Arquivo | Descrição |
|---------|-----------|
| `extract_bp_bq.py` | Extrai planilha BP Excel → tabela `orcamento_dre`. DRE_MAP com linhas fixas da planilha do Koti |
| `BP.xlsx` | Planilha Business Plan 2026 (Realizado + Orçado). Layout específico do Koti |

### Dashboard legado (GitHub Pages — será descontinuado)

| Arquivo | Descrição |
|---------|-----------|
| `dashboard_omie.html` | Dashboard original (lê `.enc` criptografados) |
| `omie_sync.py` | Sync v6 → JSON local |
| `extract_orcamento.py` | BP → JSON local |
| `encrypt_data.py` | Criptografa JSON → `.enc` |
| `index.html` | Landing page |
| `.github/workflows/sync_omie.yml` | Workflow legado |

### Outros

| Arquivo | Descrição |
|---------|-----------|
| `dashboard_streamlit.py` | Dashboard Streamlit (experimental, visual inferior ao HTML) |
| `.streamlit/config.toml` | Tema escuro Streamlit |
| `setup_gcp.sh` | Script de setup GCP |
| `dados_omie.enc` / `dados_orcamento.enc` | Dados criptografados (legado) |

---

## 3. BigQuery — Schema

**Projeto**: `dashboard-koti-omie`

Cada cliente tem seu próprio dataset (ex: `studio_koti`, `cliente_2`, etc.) com as mesmas tabelas.

### Tabelas

| # | Tabela | Registros (Koti) | Estratégia | Partição |
|:-:|--------|:----------------:|:----------:|----------|
| 1 | `lancamentos` | ~10.700 | WRITE_TRUNCATE | `sync_date` |
| 2 | `saldos_bancarios` | ~17 | WRITE_TRUNCATE | `sync_date` |
| 3 | `historico_saldos` | acumula | WRITE_APPEND | `sync_date` |
| 4 | `categorias` | ~142 | WRITE_TRUNCATE | — |
| 5 | `projetos` | ~214 | WRITE_TRUNCATE | — |
| 6 | `clientes` | ~1.837 | WRITE_TRUNCATE | — |
| 7 | `vendas_pedidos` | variável | WRITE_TRUNCATE | `sync_date` |
| 8 | `orcamento_dre` | ~336 | WRITE_TRUNCATE | — |
| 9 | `sync_log` | acumula | WRITE_APPEND | — |

### View

| View | Descrição |
|------|-----------|
| `v_historico_saldos` | Dedup de `historico_saldos` por `(conta_id, data_referencia, tipo)` |

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

## 4. Dashboard — Abas

| # | Aba | Dados | Genérica? |
|:-:|-----|-------|:---------:|
| 1 | **Visão Geral** | KPIs (saldo, entradas, saídas), saldo por conta, fluxo mensal, top categorias | Sim |
| 2 | **Fluxo de Caixa** | Despesas/receitas por grupo, pivot categoria x mês (consolidado/mês a mês) | Sim |
| 3 | **Financeiro** | Receita vs custo vs SG&A, resultado mensal, contas a receber/pagar | Sim |
| 4 | **Conciliação** | % conciliado, cards por conta, evolução (mensal/diário) | Sim |
| 5 | **Vendas** | Total, pedidos, ticket médio, por etapa, top produtos | Sim |
| 6 | **Clientes** | Total, ativos, PF/PJ, por estado, novos por mês | Sim |
| 7 | **Projetos** | Receita vs custo por projeto, busca, tabela detalhada | Sim |
| 8 | **Real vs Orçado** | Receita/EBITDA real vs BP, waterfall, % atingimento, DRE comparativo, timeline com régua | **Koti-specific** |

### Aba Real vs Orçado (Koti-specific)

Esta aba depende da tabela `orcamento_dre`, que é alimentada pelo `extract_bp_bq.py` a partir da planilha `BP.xlsx` do Studio Koti.

**Para outros clientes:**
- Se o cliente **tem planilha BP** com layout semelhante: adaptar `DRE_MAP` no `extract_bp_bq.py` com os números de linha corretos da planilha do cliente
- Se o cliente **não tem BP**: a aba mostra "Dados de orçamento não disponíveis" automaticamente (graceful fallback)
- O `api_bq.py` só inclui o campo `orcamento` no JSON se a tabela tiver dados

**Régua de período:**
- Timeline clicável com 12 meses (Jan-Dez)
- Meses com dados reais marcados em verde
- Modo **Acumulado** (Jan→mês selecionado) ou **Mês a mês** (só o mês)
- Todos os componentes respondem: KPIs, gráficos Receita/EBITDA, waterfall, % atingimento, tabela DRE

**DRE_MAP** (linhas da planilha BP do Koti — buscar `⚡ KOTI-SPECIFIC`):

```
Receita Bruta, SK, BK, RT, Aditivo, Vendas RP,
Impostos, ICMS, Crédito de ICMS, ISS, PIS/COFINS,
Receita Líquida,
Custos Operacionais, Comissões Externas/Internas, Obras,
Margem de Contribuição,
Despesas Gerais e Adm (Salários, Administrativas, Comerciais, Imóvel, Veículos, Diretoria),
EBITDA, Receitas/Despesas Financeiras, IRPJ/CSLL, Lucro Líquido
```

---

## 5. API (`api_bq.py`)

### Rotas

| Rota | Método | Resposta |
|------|--------|----------|
| `/` | GET | `dashboard_bq.html` (text/html) |
| `/api/dashboard` | GET | JSON com todos os dados do BigQuery |
| `/api` | GET | Alias para `/api/dashboard` |

### Rodar localmente

```bash
GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-key.json \
GCP_PROJECT_ID=dashboard-koti-omie \
BQ_DATASET=studio_koti \
python3 api_bq.py
# Acesse http://localhost:8080
```

### Deploy como Cloud Function

```bash
gcloud functions deploy api_dashboard \
  --runtime python311 \
  --trigger-http \
  --allow-unauthenticated \
  --entry-point api_dashboard \
  --source . \
  --set-env-vars GCP_PROJECT_ID=dashboard-koti-omie,BQ_DATASET=studio_koti \
  --region us-central1 \
  --memory 256MB \
  --timeout 60s
```

### JSON retornado

```json
{
  "atualizado_em": "ISO datetime",
  "atualizado_em_formatado": "DD/MM/YYYY às HH:MM",
  "lancamentos": [{"id", "valor", "tipo", "status", "data" (DD/MM/YYYY), "categoria", "categoria_nome", "projeto", "projeto_nome", "cliente_nome"}],
  "saldos_bancarios": [{"id", "nome", "tipo", "saldo", "saldo_conciliado", "diferenca", "data" (DD/MM/YYYY)}],
  "historico_conciliacao": [{"banco_id", "banco_nome", "data" (YYYY-MM-DD), "label", "saldo_atual", "saldo_conciliado", "diferenca", "tipo"}],
  "categorias": {"codigo": "nome"},
  "projetos": [{"id", "nome"}],
  "vendas": {"total_vendas", "quantidade_pedidos", "ticket_medio", "por_mes", "por_etapa", "top_produtos"},
  "clientes": {"total_clientes", "ativos", "inativos", "pessoa_fisica", "pessoa_juridica", "por_estado", "por_mes_cadastro"},
  "orcamento": {"meses_disponiveis", "meses_com_real", "dre": [{"label", "section", "level", "bp": {mes: valor}, "real": {mes: valor}}]}
}
```

O campo `orcamento` só é incluído se a tabela `orcamento_dre` tiver dados.

---

## 6. Pipeline de Sync

### `omie_sync_bq.py`

1. Valida credenciais (OMIE_APP_KEY/SECRET, GCP_PROJECT_ID)
2. `ensure_tables()` — cria dataset, 9 tabelas e view via DDL
3. Registra sync no `sync_log` (status=running)
4. Coleta API Omie: Categorias → Projetos → Saldos (com cache BQ) → Clientes → Lançamentos → Vendas
5. Carrega no BigQuery (TRUNCATE ou APPEND)
6. Registra sucesso/falha no `sync_log`

### GitHub Actions (`sync_omie_bq.yml`)

- **Cron**: `0 8 * * *` (5h BRT / 8h UTC)
- **Retry**: 2 tentativas, 60s entre elas, timeout 30min
- Pipeline legado roda em paralelo (será descontinuado)

### Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|----------|:-----------:|-----------|
| `OMIE_APP_KEY` | Sim | Chave da API Omie |
| `OMIE_APP_SECRET` | Sim | Secret da API Omie |
| `GCP_PROJECT_ID` | Sim | Projeto GCP |
| `BQ_DATASET` | Não | Dataset BigQuery (default: `studio_koti`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Sim (local) | Path para JSON da service account |

---

## 7. Lógica Koti-Specific

Todas marcadas com `# ⚡ KOTI-SPECIFIC` no código. Buscar e adaptar/remover para outros clientes.

| Item | Arquivo | O que fazer para novo cliente |
|------|---------|-------------------------------|
| `CONTAS_IGNORAR = {8754849088}` | `omie_sync_bq.py` | Substituir por contas fictícias do novo cliente (ou deixar vazio) |
| `is_faturamento_direto` | `omie_sync_bq.py` | Remover ou adaptar lógica FD |
| `DRE_MAP` (linhas fixas da planilha) | `extract_bp_bq.py` | Ajustar números de linha para a planilha BP do novo cliente |
| `MONTH_COLS` (ano 2026) | `extract_bp_bq.py` | Ajustar ano e colunas |
| `PASS_HASH` (senha do dashboard) | `dashboard_bq.html` | Gerar novo hash SHA-256 para a senha do cliente |
| Aba "Real vs Orçado" | `dashboard_bq.html` | Funciona automaticamente se `orcamento_dre` tiver dados; caso contrário mostra fallback |

**Status dos lançamentos** (Omie, genérico para todos os clientes):
PAGO, RECEBIDO, A VENCER, ATRASADO, VENCE HOJE, CANCELADO

---

## 8. Novo Cliente Omie — Checklist

### Passo 1: BigQuery

```bash
# Criar dataset para o novo cliente
bq mk --dataset --location=US dashboard-koti-omie:nome_cliente
```

O `omie_sync_bq.py` cria tabelas e view automaticamente no primeiro sync via `ensure_tables()`.

### Passo 2: Repositório

- [ ] Fork ou copiar o repo
- [ ] Configurar GitHub Secrets:
  - `OMIE_APP_KEY` e `OMIE_APP_SECRET` do novo cliente
  - `GCP_PROJECT_ID` = `dashboard-koti-omie`
  - `GCP_SA_KEY` = JSON service account (base64)
- [ ] Alterar `BQ_DATASET` no workflow para `nome_cliente`
- [ ] Buscar `⚡ KOTI-SPECIFIC` e adaptar/remover (ver tabela na seção 7)

### Passo 3: Orçamento (opcional)

Se o cliente tem planilha Business Plan:
- [ ] Adaptar `DRE_MAP` no `extract_bp_bq.py` com os números de linha da planilha do cliente
- [ ] Colocar `BP.xlsx` na raiz do repo

Se não tem:
- [ ] Não incluir `extract_bp_bq.py` no workflow — a aba "Real vs Orçado" mostra fallback automaticamente

### Passo 4: Dashboard

- [ ] Deploy Cloud Function com `BQ_DATASET=nome_cliente`
- [ ] Copiar `dashboard_bq.html` e atualizar `PASS_HASH` com nova senha
- [ ] Ou: usar mesma API com parâmetro de dataset (`/api/dashboard?dataset=nome_cliente`)

### Estrutura multi-cliente

```
Projeto GCP: dashboard-koti-omie
├── Dataset: studio_koti     ← Studio Koti (primeiro cliente)
├── Dataset: cliente_2       ← Segundo cliente
├── Dataset: cliente_3       ← Terceiro cliente
└── Dataset: consolidado     ← Views cross-dataset (opcional)
```

Cada dataset tem as mesmas 8-9 tabelas (a `orcamento_dre` é opcional).

### Visão consolidada (opcional)

```sql
CREATE OR REPLACE VIEW `dashboard-koti-omie.consolidado.lancamentos_all` AS
SELECT 'Studio Koti' AS cliente, * FROM `studio_koti.lancamentos`
UNION ALL
SELECT 'Cliente 2' AS cliente, * FROM `cliente_2.lancamentos`;
```

---

## 9. GitHub Secrets

| Secret | Valor | Usado por |
|--------|-------|-----------|
| `OMIE_APP_KEY` | Chave API Omie do cliente | `omie_sync_bq.py` |
| `OMIE_APP_SECRET` | Secret API Omie do cliente | `omie_sync_bq.py` |
| `GCP_PROJECT_ID` | `dashboard-koti-omie` | todos os scripts BQ |
| `GCP_SA_KEY` | JSON service account (base64) | workflow |
| `DASHBOARD_PASSWORD` | Senha do dashboard legado | `encrypt_data.py` |

Para encodar a chave:
```bash
cat gcp-key.json | base64 | pbcopy
```

---

## 10. Troubleshooting

| Problema | Causa | Solução |
|----------|-------|---------|
| API retorna 403 | SA sem permissão | Adicionar roles `bigquery.dataEditor` + `bigquery.jobUser` |
| API retorna "Table not found" | Tabelas não criadas | Rodar `omie_sync_bq.py` (cria via `ensure_tables()`) ou criar view manualmente |
| Dashboard "Erro ao conectar" | API não rodando | Verificar se `api_bq.py` está rodando e acessível |
| Aba Orçamento vazia | `orcamento_dre` sem dados | Rodar `extract_bp_bq.py` com `BP.xlsx` no repo. Ou: cliente não tem BP (esperado) |
| Régua do orçamento não muda gráficos | Bug anterior (corrigido) | Atualizar `dashboard_bq.html` para versão mais recente |
| Sync falhou | API Omie fora do ar | Verificar credenciais nos Secrets. O workflow tem retry automático |
| Histórico duplicado | WRITE_APPEND | Usar view `v_historico_saldos` (já é o padrão) |
| Sync lento (>30min) | Muitas chamadas API | Cache BQ de histórico reduz chamadas. Timeout: 40min |

---

## 11. Custos

| Item | Custo |
|------|-------|
| BigQuery storage (por cliente) | ~R$ 0 (free tier < 10 GB) |
| BigQuery queries | ~R$ 0 (free tier: 1 TB/mês) |
| Cloud Functions | Grátis (2M invocações/mês) |
| GitHub Actions | Grátis (2000 min/mês) |
| **Total por cliente** | **~R$ 0/mês** |

Com 10+ clientes pesados, pode sair do free tier (~R$ 5-20/mês adicional).

---

## 12. Próximos Passos

- [ ] Deploy da Cloud Function (`api_bq.py`) para acesso externo
- [ ] Hospedar dashboard (Cloud Function já serve na `/`, ou usar Cloud Storage/Vercel)
- [ ] Onboarding segundo cliente Omie
- [ ] Fase 2: Agente WhatsApp (Claude API + WhatsApp Business API + BigQuery)
- [ ] Descontinuar pipeline legado (GitHub Pages)
