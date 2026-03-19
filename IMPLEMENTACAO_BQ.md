# Dashboard Financeiro — Studio Koti

**Projeto GCP**: `dashboard-koti-omie`
**Dataset BigQuery**: `studio_koti`
**Repositório**: https://github.com/akliot/dashboard-koti
**Última atualização**: 19/03/2026

---

## 1. Arquitetura

```
┌──────────┐     ┌─────────────────┐     ┌─────────────┐     ┌────────────────┐
│ API Omie │────▶│ omie_sync_bq.py │────▶│  BigQuery    │────▶│   Streamlit    │
│ (ERP)    │     │ (GitHub Actions) │     │  (GCP)      │     │  (Dashboard)   │
└──────────┘     └─────────────────┘     └──────┬──────┘     └────────────────┘
                                                │
                  ┌─────────────────┐           │
                  │ extract_bp_bq.py│──────────▶│
                  │ (BP → BigQuery)  │           │
                  └─────────────────┘           │
                                                │
                                         ┌──────┴──────┐
                                         │  GitHub     │
                                         │  Pages      │
                                         │ (legacy —   │
                                         │  paralelo)  │
                                         └─────────────┘
```

**Fluxo diário:**
1. GitHub Actions roda às **5h BRT** (8h UTC)
2. `omie_sync_bq.py` coleta dados da API Omie → escreve em 8 tabelas BigQuery
3. `extract_bp_bq.py` extrai planilha BP Excel → tabela `orcamento_dre`
4. Dashboard Streamlit lê do BigQuery em tempo real
5. Pipeline legado (GitHub Pages) roda em paralelo

---

## 2. Arquivos do Projeto

### Pipeline BigQuery (novos)

| Arquivo | Descrição |
|---------|-----------|
| `omie_sync_bq.py` | Script principal — coleta API Omie → BigQuery (9 tabelas + 1 view). Inclui `ensure_tables()` que cria DDL automaticamente |
| `extract_bp_bq.py` | Extrai DRE Real vs Orçado da planilha Excel → tabela `orcamento_dre` |
| `bq_schema.sql` | DDL de referência com 9 CREATE TABLE + 1 CREATE VIEW (para execução manual no Console) |
| `.github/workflows/sync_omie_bq.yml` | Workflow GitHub Actions (BQ + legacy em paralelo) |
| `requirements_bq.txt` | Dependências: requests, google-cloud-bigquery, openpyxl, db-dtypes, streamlit, plotly, pandas |

### Dashboard Streamlit (novo)

| Arquivo | Descrição |
|---------|-----------|
| `dashboard_streamlit.py` | Dashboard com 7 páginas, tema escuro, filtros globais, proteção por senha |
| `.streamlit/config.toml` | Tema escuro (cores do dashboard_omie.html original) |

### Pipeline legado (GitHub Pages — não modificados)

| Arquivo | Descrição |
|---------|-----------|
| `omie_sync.py` | Sync v6 incremental → JSON local |
| `extract_orcamento.py` | Extrai BP → JSON local |
| `encrypt_data.py` | Criptografa JSON → `.enc` para GitHub Pages |
| `dashboard_omie.html` | Dashboard HTML/JS original (Chart.js) |
| `index.html` | Landing page com redirect |
| `.github/workflows/sync_omie.yml` | Workflow legado (apenas GitHub Pages) |

### Outros

| Arquivo | Descrição |
|---------|-----------|
| `BP.xlsx` | Planilha Business Plan (Realizado + Orçado) |
| `dados_omie.enc` | Dados Omie criptografados (legado) |
| `dados_orcamento.enc` | Dados orçamento criptografados (legado) |
| `setup_gcp.sh` | Script de setup do GCP (criar projeto, SA, dataset) |
| `.gitignore` | Exclui secrets, JSON em texto plano, .streamlit/secrets.toml |

---

## 3. BigQuery — Schema Completo

**Projeto**: `dashboard-koti-omie`
**Dataset**: `studio_koti`

### Tabelas

| # | Tabela | Registros | Estratégia | Partição | Cluster |
|:-:|--------|:---------:|:----------:|----------|---------|
| 1 | `lancamentos` | ~10.700 | WRITE_TRUNCATE | `sync_date` | `tipo`, `categoria_grupo` |
| 2 | `saldos_bancarios` | ~17 | WRITE_TRUNCATE | `sync_date` | — |
| 3 | `historico_saldos` | acumula | WRITE_APPEND | `sync_date` | `conta_id`, `tipo` |
| 4 | `categorias` | ~142 | WRITE_TRUNCATE | — | — |
| 5 | `projetos` | ~214 | WRITE_TRUNCATE | — | — |
| 6 | `clientes` | ~1.830 | WRITE_TRUNCATE | — | — |
| 7 | `vendas_pedidos` | variável | WRITE_TRUNCATE | `sync_date` | — |
| 8 | `orcamento_dre` | ~312 | WRITE_TRUNCATE | — | — |
| 9 | `sync_log` | acumula | WRITE_APPEND | — | — |

### View

| View | Descrição |
|------|-----------|
| `v_historico_saldos` | Dedup de `historico_saldos` por `(conta_id, data_referencia, tipo)` — mantém último `sync_timestamp` |

### Campos por tabela

**`lancamentos`** — Todos os lançamentos financeiros

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | INT64 | ID do lançamento no Omie |
| `tipo` | STRING | `'entrada'` (receber) ou `'saida'` (pagar) |
| `valor` | FLOAT64 | Valor em R$ |
| `status` | STRING | PAGO, RECEBIDO, A VENCER, ATRASADO, VENCE HOJE, CANCELADO |
| `data_vencimento` | DATE | Data de vencimento |
| `data_emissao` | DATE | Data de emissão (competência) |
| `numero_documento` | STRING | Número do documento/NF |
| `categoria_codigo` | STRING | Código da categoria (ex: 1.01.02) |
| `categoria_nome` | STRING | Nome da categoria (ex: Marcenaria) |
| `categoria_grupo` | STRING | Grupo — 2 primeiros níveis (ex: 1.01) |
| `projeto_id` | INT64 | ID do projeto/obra |
| `projeto_nome` | STRING | Nome do projeto/obra |
| `cliente_id` | INT64 | ID do cliente/fornecedor |
| `cliente_nome` | STRING | Nome fantasia ou razão social |
| `conta_corrente_id` | INT64 | ID da conta bancária |
| `is_faturamento_direto` | BOOL | True se faturamento direto (Koti-specific) |
| `sync_timestamp` | TIMESTAMP | Timestamp do sync |
| `sync_date` | DATE | Data do sync (partição) |

**`saldos_bancarios`** — Snapshot D-1 de cada conta

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `conta_id` | INT64 | ID da conta corrente |
| `conta_nome` | STRING | Nome da conta |
| `conta_tipo` | STRING | Tipo da conta |
| `saldo` | FLOAT64 | Saldo atual (nSaldoAtual) |
| `saldo_conciliado` | FLOAT64 | Saldo conciliado |
| `diferenca` | FLOAT64 | saldo - saldo_conciliado |
| `data_referencia` | DATE | Data de referência (D-1) |
| `sync_timestamp` | TIMESTAMP | Timestamp do sync |
| `sync_date` | DATE | Data do sync |

**`historico_saldos`** — Evolução mensal e diária dos saldos

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `conta_id` | INT64 | ID da conta |
| `conta_nome` | STRING | Nome da conta |
| `data_referencia` | DATE | Data do snapshot |
| `label` | STRING | Label formatado (ex: Jan/26 ou 15/01) |
| `saldo_atual` | FLOAT64 | Saldo atual |
| `saldo_conciliado` | FLOAT64 | Saldo conciliado |
| `diferenca` | FLOAT64 | Diferença |
| `tipo` | STRING | `'mensal'` ou `'diario'` |
| `sync_timestamp` | TIMESTAMP | Timestamp do sync |
| `sync_date` | DATE | Data do sync |

**`categorias`** — Categorias contábeis

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `codigo` | STRING | Código (ex: 1.01.010) |
| `nome` | STRING | Nome limpo (sem prefixo numérico) |
| `grupo` | STRING | Grupo (2 primeiros níveis) |
| `sync_timestamp` | TIMESTAMP | Timestamp do sync |

**`projetos`** — Projetos/obras

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | INT64 | ID do projeto |
| `nome` | STRING | Nome do projeto |
| `sync_timestamp` | TIMESTAMP | Timestamp do sync |

**`clientes`** — Cadastro de clientes/fornecedores (1 linha por cliente)

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | INT64 | codigo_cliente_omie |
| `nome_fantasia` | STRING | Nome fantasia |
| `razao_social` | STRING | Razão social |
| `estado` | STRING | UF |
| `ativo` | BOOL | true se inativo='N' |
| `pessoa_fisica` | BOOL | true se pessoa_fisica='S' |
| `data_cadastro` | DATE | Data de inclusão |
| `sync_timestamp` | TIMESTAMP | Timestamp do sync |

**`vendas_pedidos`** — Pedidos de venda (1 linha por item)

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `pedido_id` | INT64 | ID do pedido |
| `valor_mercadorias` | FLOAT64 | Valor total de mercadorias |
| `etapa` | STRING | Em Aberto, Aprovado, Faturado, Cancelado, Entregue |
| `data_previsao` | DATE | Data prevista |
| `produto_descricao` | STRING | Descrição do produto |
| `produto_quantidade` | FLOAT64 | Quantidade do item |
| `produto_valor_total` | FLOAT64 | Valor total do item |
| `sync_timestamp` | TIMESTAMP | Timestamp do sync |
| `sync_date` | DATE | Data do sync |

**`orcamento_dre`** — DRE Real vs Orçado (1 linha por item x mês)

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `label` | STRING | Nome da linha DRE (ex: Receita Bruta, EBITDA) |
| `section` | STRING | Seção: receita, impostos, custos, margem, sga, ebitda, ll |
| `level` | INT64 | 0=total, 1=subtotal, 2=detalhe |
| `mes` | STRING | Mês (YYYY-MM) |
| `valor_real` | FLOAT64 | Valor realizado |
| `valor_bp` | FLOAT64 | Valor orçado |
| `variacao_pct` | FLOAT64 | (real - bp) / abs(bp) * 100, NULL se bp=0 |
| `mes_com_real` | BOOL | true se o mês tem dados reais |
| `sync_timestamp` | TIMESTAMP | Timestamp do sync |

**`sync_log`** — Log de execução de cada sync

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `sync_id` | STRING | UUID do sync |
| `started_at` | TIMESTAMP | Início |
| `finished_at` | TIMESTAMP | Fim |
| `status` | STRING | running, success, failed |
| `duration_seconds` | INT64 | Duração em segundos |
| `lancamentos_count` | INT64 | Contagem de lançamentos |
| `saldos_count` | INT64 | Contagem de saldos |
| `clientes_count` | INT64 | Contagem de clientes |
| `projetos_count` | INT64 | Contagem de projetos |
| `categorias_count` | INT64 | Contagem de categorias |
| `error_message` | STRING | Erro (se failed) |
| `is_incremental` | BOOL | true se usou cache |

---

## 4. Pipeline de Sync (`omie_sync_bq.py`)

### Fluxo de execução

1. Valida variáveis de ambiente (`OMIE_APP_KEY`, `OMIE_APP_SECRET`, `GCP_PROJECT_ID`)
2. Inicializa BigQuery client
3. `ensure_tables()` — cria dataset, tabelas e view se não existirem (DDL)
4. Registra sync no `sync_log` (status=running)
5. Coleta dados da API Omie:
   - Categorias (`ListarCategorias` + `ConsultarCategoria` para faltantes)
   - Projetos (`ListarProjetos`)
   - Saldos bancários (`ListarExtrato` — snapshot D-1 + histórico mensal/diário com cache BQ)
   - Clientes (`ListarClientes` — bulk)
   - Lançamentos (`ListarContasReceber` + `ListarContasPagar`)
   - Vendas (`ListarPedidos` — 1 linha por item)
6. Transforma dados para schema BigQuery
7. Carrega em cada tabela (WRITE_TRUNCATE ou WRITE_APPEND)
8. Registra sucesso/falha no `sync_log`

### Cache inteligente (histórico de saldos)

O `historico_saldos` usa WRITE_APPEND. Para evitar chamadas desnecessárias à API:
- Consulta BigQuery para saber quais `(conta_id, data_referencia)` mensais já existem
- Pula chamadas para meses passados já registrados
- Sempre refaz dados do mês corrente (ainda mudam)
- Deduplicação final delegada à view `v_historico_saldos`

### Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|----------|:-----------:|-----------|
| `OMIE_APP_KEY` | Sim | Chave da API Omie |
| `OMIE_APP_SECRET` | Sim | Secret da API Omie |
| `GCP_PROJECT_ID` | Sim | Projeto GCP (ex: `dashboard-koti-omie`) |
| `BQ_DATASET` | Não | Dataset BigQuery (default: `studio_koti`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Sim | Path para JSON da service account |

### Rate limits e retry

- Sleep de 0.05s entre chamadas individuais
- Retry com backoff: 2s × tentativa (max 3 tentativas)
- Timeout por request: 60s
- Workflow com retry: 2 tentativas, 60s entre elas

---

## 5. GitHub Actions (`sync_omie_bq.yml`)

### Schedule
- **Cron**: `0 8 * * *` (5h BRT / 8h UTC)
- **Manual**: workflow_dispatch

### Steps
1. Checkout + Python 3.11
2. Install dependencies
3. Decode `GCP_SA_KEY` (base64) → `/tmp/gcp-key.json`
4. Run `omie_sync_bq.py` (com retry: 2 tentativas, 30min timeout)
5. Run `extract_bp_bq.py` (sem retry, falha silenciosa)
6. Cleanup `/tmp/gcp-key.json`
7. **Legacy**: `omie_sync.py` → `extract_orcamento.py` → `encrypt_data.py` → commit/push `.enc`

---

## 6. Dashboard Streamlit

### 7 Páginas

| # | Página | Componentes principais |
|:-:|--------|----------------------|
| 1 | **Visão Geral** | KPIs (saldo total, entradas, saídas, resultado), saldo por conta (barras), fluxo mensal (barras agrupadas), top categorias |
| 2 | **Fluxo de Caixa** | KPIs, donuts despesas/receitas por grupo, pivot table categoria × mês |
| 3 | **Financeiro** | Receita vs despesa (barras empilhadas), resultado mensal, tabelas contas a receber/pagar |
| 4 | **Conciliação** | % conciliado, cards por conta (cor por status), evolução conciliação (linha) |
| 5 | **Vendas** | KPIs (total, pedidos, ticket médio), vendas por etapa, top produtos |
| 6 | **Projetos** | Busca, receita vs custo por projeto, tabela com margem % |
| 7 | **Real vs Orçado** | % atingimento (receita, EBITDA, LL), gráficos real vs BP, tabela DRE comparativa |

### Filtros globais (sidebar)
- **Período**: atalhos (Mês, Trimestre, YTD, Ano, Tudo) + date inputs
- **Projeto**: dropdown com todos os projetos
- **Tipo**: Todos / Entrada / Saída

### Proteção por senha
- Usa `st.secrets["dashboard_password"]` (Streamlit Cloud)
- Fallback: variável de ambiente `DASHBOARD_PASSWORD`
- Se nenhum configurado: modo dev (sem autenticação)
- Estado de login em `st.session_state`

### Tema visual
- Fundo escuro: `#0f172a` (bg), `#1e293b` (cards), `#334155` (bordas)
- Texto: `#e2e8f0` (principal), `#94a3b8` (muted)
- Cores: `#3b82f6` (accent/azul), `#22c55e` (verde/entrada), `#ef4444` (vermelho/saída)
- Gráficos Plotly com layout dark consistente

### Rodar localmente

```bash
# Instalar dependências
pip install -r requirements_bq.txt

# Gerar chave GCP (se não tiver)
~/google-cloud-sdk/bin/gcloud iam service-accounts keys create /tmp/gcp-key.json \
  --iam-account=omie-sync@dashboard-koti-omie.iam.gserviceaccount.com

# Rodar
GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-key.json \
GCP_PROJECT_ID=dashboard-koti-omie \
streamlit run dashboard_streamlit.py
```

Acessa em http://localhost:8501.

### Deploy no Streamlit Community Cloud

1. Push repo para GitHub
2. Em [share.streamlit.io](https://share.streamlit.io): conectar repo, apontar para `dashboard_streamlit.py`
3. Em Settings > Secrets, adicionar:
```toml
[gcp_service_account]
type = "service_account"
project_id = "dashboard-koti-omie"
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "omie-sync@dashboard-koti-omie.iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"

dashboard_password = "sua-senha-aqui"
```
4. O `.streamlit/secrets.toml` está no `.gitignore` — nunca commitar

---

## 7. Lógica Koti-Specific

Todas as regras específicas do Studio Koti estão marcadas com `# ⚡ KOTI-SPECIFIC` no código.

### CONTAS_IGNORAR

```python
CONTAS_IGNORAR = {
    8754849088,  # BAIXA DE NFS - conta fictícia para baixa de notas
}
```

Lançamentos vinculados a essas contas são descartados (não representam movimentação real).

### is_faturamento_direto

| Tipo | Regra |
|------|-------|
| `entrada` (receber) | `True` se `categoria_nome` contém "Faturamento Direto" (case-insensitive) |
| `saida` (pagar) | `True` se `numero_documento` contém "FD" (case-insensitive) |

### Status dos lançamentos

Valores reais encontrados no Omie do Studio Koti:

| Status | Contagem aprox. | Descrição |
|--------|:---------------:|-----------|
| `PAGO` | ~7.926 | Conta paga (saída liquidada) |
| `RECEBIDO` | ~1.075 | Conta recebida (entrada liquidada) |
| `A VENCER` | ~1.229 | Pendente, dentro do prazo |
| `ATRASADO` | ~340 | Vencido e não pago/recebido |
| `VENCE HOJE` | ~91 | Vence no dia corrente |
| `CANCELADO` | ~40 | Cancelado |

---

## 8. GitHub Secrets

| Secret | Valor | Usado por |
|--------|-------|-----------|
| `OMIE_APP_KEY` | Chave API Omie | `omie_sync_bq.py`, `omie_sync.py` |
| `OMIE_APP_SECRET` | Secret API Omie | `omie_sync_bq.py`, `omie_sync.py` |
| `GCP_PROJECT_ID` | `dashboard-koti-omie` | `omie_sync_bq.py`, `extract_bp_bq.py` |
| `GCP_SA_KEY` | JSON da service account (base64) | workflow (decodifica → `/tmp/gcp-key.json`) |
| `DASHBOARD_PASSWORD` | Senha do dashboard HTML legado | `encrypt_data.py` |

Para encodar a chave em base64:
```bash
cat gcp-key.json | base64 | pbcopy  # cola no GitHub Secret
```

---

## 9. Checklist — Novo Cliente

Para replicar todo o sistema para outro cliente Omie:

### GCP
- [ ] Criar dataset: `bq mk --dataset --location=US dashboard-koti-omie:nome_cliente`
- [ ] Executar os CREATE TABLE do `bq_schema.sql` trocando `studio_koti` por `nome_cliente`

### Repositório
- [ ] Fork ou criar novo repo com os mesmos scripts
- [ ] Configurar GitHub Secrets:
  - `OMIE_APP_KEY` e `OMIE_APP_SECRET` do novo cliente
  - `GCP_PROJECT_ID` = `dashboard-koti-omie` (mesmo projeto)
  - `GCP_SA_KEY` (mesma service account ou criar nova)
- [ ] Alterar `BQ_DATASET` no workflow para `nome_cliente`
- [ ] Revisar/remover regras Koti-specific:
  - `CONTAS_IGNORAR` — adaptar contas fictícias do novo cliente
  - `is_faturamento_direto` — remover ou adaptar lógica FD
  - `DRE_MAP` no `extract_bp_bq.py` — ajustar linhas da planilha BP do novo cliente

### Dashboard
- [ ] Duplicar dashboard Streamlit ou apontar para novo dataset
- [ ] Configurar senha
- [ ] Deploy no Streamlit Cloud (ou adicionar página de seleção de cliente)

### Visão consolidada (opcional)

```sql
CREATE OR REPLACE VIEW `dashboard-koti-omie.consolidado.lancamentos_all` AS
SELECT 'Studio Koti' AS cliente, * FROM `studio_koti.lancamentos`
UNION ALL
SELECT 'Cliente 2' AS cliente, * FROM `cliente_2.lancamentos`;
```

---

## 10. Troubleshooting

### Erro: "403 Access Denied" no BigQuery

**Causa**: Service account sem permissão.
**Solução**:
```bash
gcloud projects add-iam-policy-binding dashboard-koti-omie \
  --member="serviceAccount:omie-sync@dashboard-koti-omie.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataEditor"

gcloud projects add-iam-policy-binding dashboard-koti-omie \
  --member="serviceAccount:omie-sync@dashboard-koti-omie.iam.gserviceaccount.com" \
  --role="roles/bigquery.jobUser"
```

### Erro: "Not found: Table/Dataset"

**Causa**: Tabelas ainda não criadas.
**Solução**: O `omie_sync_bq.py` cria automaticamente via `ensure_tables()`. Se precisar criar manualmente, execute `bq_schema.sql` no BigQuery Console.

### Sync falhou com "Nenhum dado coletado"

**Causa**: API Omie fora do ar ou credenciais inválidas.
**Solução**: Verificar `OMIE_APP_KEY` e `OMIE_APP_SECRET` nos GitHub Secrets. Testar manualmente:
```bash
curl -X POST https://app.omie.com.br/api/v1/geral/categorias/ \
  -H "Content-Type: application/json" \
  -d '{"call":"ListarCategorias","app_key":"SUA_KEY","app_secret":"SEU_SECRET","param":[{"pagina":1,"registros_por_pagina":1}]}'
```

### Dashboard Streamlit não carrega dados

**Causa**: Credenciais GCP não configuradas.
**Solução**:
- Local: verificar `GOOGLE_APPLICATION_CREDENTIALS` aponta para JSON válido
- Streamlit Cloud: verificar `[gcp_service_account]` em Settings > Secrets
- Testar conexão: `python -c "from google.cloud import bigquery; print(bigquery.Client(project='dashboard-koti-omie').query('SELECT 1').result())"`

### Histórico de saldos duplicado

**Causa**: `historico_saldos` usa WRITE_APPEND.
**Solução**: Sempre usar a view `v_historico_saldos` (dedup automático). O dashboard Streamlit já faz isso.

### Planilha BP não processada

**Causa**: `BP.xlsx` não encontrada no diretório do script.
**Solução**: Colocar `BP.xlsx` (ou `BP*.xlsx`) na raiz do repo. O script retorna exit 0 se não encontrar (não quebra o workflow).

### Workflow legado falhou

**Causa**: Conflito de merge no push.
**Solução**: O workflow já faz `git pull --rebase` antes do push. Se persistir, verificar se outro processo está commitando no mesmo branch.

### Sync muito lento (>30min)

**Causa**: Muitas chamadas à API (ListarContasPagar tem ~116 páginas).
**Solução**: O timeout do workflow é 40min. O cache BQ de histórico de saldos reduz chamadas. Se persistir, considerar reduzir `registros_por_pagina` ou o range de meses do histórico.

---

## 11. Custos

| Item | Custo |
|------|-------|
| BigQuery storage (1 cliente) | ~R$ 0 (< 10 GB = free tier) |
| BigQuery queries | ~R$ 0 (free tier: 1 TB/mês) |
| Streamlit Community Cloud | Grátis |
| GitHub Actions | Grátis (2000 min/mês) |
| **Total** | **~R$ 0/mês** |

Com 10+ clientes pesados, pode sair do free tier (~R$ 5-20/mês por cliente adicional).
