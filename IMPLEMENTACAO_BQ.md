# Dashboard Financeiro — Studio Koti

**Projeto GCP**: `dashboard-koti-omie`
**Dataset BigQuery**: `studio_koti`
**Repositório**: https://github.com/akliot/dashboard-koti
**Última atualização**: 19/03/2026

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

**Fluxo:**
1. GitHub Actions roda diariamente às **5h BRT** (8h UTC)
2. `omie_sync_bq.py` coleta API Omie → BigQuery (9 tabelas)
3. `extract_bp_bq.py` extrai planilha BP → tabela `orcamento_dre`
4. `api_bq.py` consulta BigQuery e serve JSON via HTTP
5. `dashboard_bq.html` faz fetch da API e renderiza o dashboard (Chart.js)

---

## 2. Arquivos do Projeto

### Pipeline BigQuery

| Arquivo | Descrição |
|---------|-----------|
| `omie_sync_bq.py` | Coleta API Omie → BigQuery. Inclui `ensure_tables()` (DDL automático) |
| `extract_bp_bq.py` | Planilha BP Excel → tabela `orcamento_dre` |
| `bq_schema.sql` | DDL de referência (9 CREATE TABLE + 1 VIEW) |
| `.github/workflows/sync_omie_bq.yml` | Workflow GitHub Actions (BQ + legacy) |
| `requirements_bq.txt` | Dependências Python |

### Dashboard (novo — BigQuery)

| Arquivo | Descrição |
|---------|-----------|
| `dashboard_bq.html` | Dashboard HTML/Chart.js — lê da API BigQuery. Visual idêntico ao original |
| `api_bq.py` | API que consulta BigQuery e retorna JSON no formato do dashboard. Roda como Cloud Function ou servidor local |

### Dashboard (legado — GitHub Pages)

| Arquivo | Descrição |
|---------|-----------|
| `dashboard_omie.html` | Dashboard original (lê dados criptografados do GitHub Pages) |
| `omie_sync.py` | Sync v6 → JSON local |
| `extract_orcamento.py` | BP → JSON local |
| `encrypt_data.py` | Criptografa JSON → `.enc` |
| `index.html` | Landing page |
| `.github/workflows/sync_omie.yml` | Workflow legado |

### Dashboard Streamlit (experimental)

| Arquivo | Descrição |
|---------|-----------|
| `dashboard_streamlit.py` | Dashboard Streamlit (7 páginas) — funcional mas visual inferior ao HTML |
| `.streamlit/config.toml` | Tema escuro Streamlit |

### Outros

| Arquivo | Descrição |
|---------|-----------|
| `BP.xlsx` | Planilha Business Plan |
| `dados_omie.enc` / `dados_orcamento.enc` | Dados criptografados (legado) |
| `setup_gcp.sh` | Script de setup GCP |

---

## 3. BigQuery — Schema

**Projeto**: `dashboard-koti-omie` | **Dataset**: `studio_koti`

### Tabelas

| # | Tabela | Registros | Estratégia | Partição |
|:-:|--------|:---------:|:----------:|----------|
| 1 | `lancamentos` | ~10.700 | WRITE_TRUNCATE | `sync_date` |
| 2 | `saldos_bancarios` | ~17 | WRITE_TRUNCATE | `sync_date` |
| 3 | `historico_saldos` | acumula | WRITE_APPEND | `sync_date` |
| 4 | `categorias` | ~142 | WRITE_TRUNCATE | — |
| 5 | `projetos` | ~214 | WRITE_TRUNCATE | — |
| 6 | `clientes` | ~1.837 | WRITE_TRUNCATE | — |
| 7 | `vendas_pedidos` | variável | WRITE_TRUNCATE | `sync_date` |
| 8 | `orcamento_dre` | ~312 | WRITE_TRUNCATE | — |
| 9 | `sync_log` | acumula | WRITE_APPEND | — |

### View

| View | Descrição |
|------|-----------|
| `v_historico_saldos` | Dedup de `historico_saldos` — mantém último sync por `(conta_id, data_referencia, tipo)` |

### Campos detalhados

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

## 4. API BigQuery (`api_bq.py`)

Serve dados do BigQuery no formato JSON idêntico ao que o `dashboard_omie.html` espera.

### Rodar localmente

```bash
GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-key.json \
GCP_PROJECT_ID=dashboard-koti-omie \
python3 api_bq.py
# API rodando em http://localhost:8080
# Abrir dashboard_bq.html no browser
```

### Deploy como Cloud Function

```bash
cd ~/dashboard-koti
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

Após deploy, atualizar a URL em `dashboard_bq.html`:
```javascript
const API_URL = 'https://us-central1-dashboard-koti-omie.cloudfunctions.net/api_dashboard';
```

### Formato do JSON retornado

```json
{
  "atualizado_em": "2026-03-19T19:09:00",
  "atualizado_em_formatado": "19/03/2026 às 19:09",
  "lancamentos": [{"id":..., "valor":..., "tipo":"entrada|saida", "status":"PAGO|RECEBIDO|...", "data":"DD/MM/YYYY", "categoria":"1.01", "categoria_nome":"...", "projeto":..., "projeto_nome":"...", "cliente_nome":"..."}],
  "saldos_bancarios": [{"id":..., "nome":"...", "tipo":"CC|CR|...", "saldo":..., "saldo_conciliado":..., "diferenca":..., "data":"DD/MM/YYYY"}],
  "historico_conciliacao": [{"banco_id":..., "banco_nome":"...", "data":"YYYY-MM-DD", "label":"Mar/26", "saldo_atual":..., "saldo_conciliado":..., "diferenca":..., "tipo":"mensal|diario"}],
  "categorias": {"1.01": "Receita de Serviços", ...},
  "projetos": [{"id":..., "nome":"..."}],
  "vendas": {"total_vendas":..., "quantidade_pedidos":..., "ticket_medio":..., "por_mes":{}, "por_etapa":{}, "top_produtos":{}},
  "clientes": {"total_clientes":..., "ativos":..., "inativos":..., "pessoa_fisica":..., "pessoa_juridica":..., "por_estado":{}, "por_mes_cadastro":{}}
}
```

---

## 5. Pipeline de Sync

### `omie_sync_bq.py` — Fluxo

1. Valida credenciais (OMIE_APP_KEY/SECRET, GCP_PROJECT_ID)
2. `ensure_tables()` — cria dataset, 9 tabelas e view via DDL
3. Registra sync no `sync_log` (status=running)
4. Coleta API Omie: Categorias → Projetos → Saldos (com cache BQ) → Clientes → Lançamentos → Vendas
5. Carrega no BigQuery (TRUNCATE ou APPEND)
6. Registra sucesso/falha no `sync_log`

### GitHub Actions (`sync_omie_bq.yml`)

- **Cron**: `0 8 * * *` (5h BRT)
- **Retry**: 2 tentativas, 60s entre elas, timeout 30min
- Pipeline legado roda em paralelo

---

## 6. Lógica Koti-Specific

Marcadas com `# ⚡ KOTI-SPECIFIC` no código.

**CONTAS_IGNORAR**: `{8754849088}` — conta fictícia BAIXA DE NFS

**is_faturamento_direto**:
- Entrada: `True` se `categoria_nome` contém "Faturamento Direto"
- Saída: `True` se `numero_documento` contém "FD"

**Status**: PAGO (~7.926), RECEBIDO (~1.075), A VENCER (~1.229), ATRASADO (~340), VENCE HOJE (~91), CANCELADO (~40)

---

## 7. GitHub Secrets

| Secret | Valor | Usado por |
|--------|-------|-----------|
| `OMIE_APP_KEY` | Chave API Omie | `omie_sync_bq.py` |
| `OMIE_APP_SECRET` | Secret API Omie | `omie_sync_bq.py` |
| `GCP_PROJECT_ID` | `dashboard-koti-omie` | scripts BQ |
| `GCP_SA_KEY` | JSON service account (base64) | workflow |
| `DASHBOARD_PASSWORD` | Senha do dashboard legado | `encrypt_data.py` |

---

## 8. Novo Cliente — Checklist

Para replicar o sistema para outra empresa Omie:

### BigQuery
- [ ] Criar dataset: `bq mk --dataset --location=US dashboard-koti-omie:nome_cliente`
- [ ] O `omie_sync_bq.py` cria tabelas automaticamente via `ensure_tables()`

### Repositório
- [ ] Fork ou criar novo repo
- [ ] Configurar GitHub Secrets (OMIE_APP_KEY/SECRET do novo cliente, GCP_PROJECT_ID, GCP_SA_KEY)
- [ ] Alterar `BQ_DATASET` no workflow para `nome_cliente`
- [ ] Revisar regras Koti-specific (buscar `⚡ KOTI-SPECIFIC`):
  - `CONTAS_IGNORAR` — adaptar contas fictícias
  - `is_faturamento_direto` — remover ou adaptar
  - `DRE_MAP` no `extract_bp_bq.py` — ajustar linhas da planilha BP

### API + Dashboard
- [ ] Deploy nova Cloud Function (ou alterar `BQ_DATASET` na existente)
- [ ] Copiar `dashboard_bq.html` e apontar `API_URL` para a nova função
- [ ] Atualizar `PASS_HASH` com senha do novo cliente

### Multi-cliente no mesmo projeto GCP

```
Projeto GCP: dashboard-koti-omie
├── Dataset: studio_koti        ← Koti
├── Dataset: cliente_2          ← Novo cliente
└── Dataset: cliente_3          ← Outro cliente
```

Cada dataset tem as mesmas 9 tabelas + 1 view. A API serve o dataset correto via `BQ_DATASET`.

### Visão consolidada (opcional)

```sql
CREATE OR REPLACE VIEW `dashboard-koti-omie.consolidado.lancamentos_all` AS
SELECT 'Studio Koti' AS cliente, * FROM `studio_koti.lancamentos`
UNION ALL
SELECT 'Cliente 2' AS cliente, * FROM `cliente_2.lancamentos`;
```

---

## 9. Troubleshooting

| Problema | Causa | Solução |
|----------|-------|---------|
| API retorna 403 | Service account sem permissão | Adicionar roles `bigquery.dataEditor` + `bigquery.jobUser` |
| API retorna "Table not found" | View/tabelas não criadas | Rodar `omie_sync_bq.py` (cria tudo via `ensure_tables()`) |
| Dashboard mostra "Erro ao conectar" | API não está rodando ou CORS | Verificar `API_URL` no HTML e se a API está acessível |
| Sync falhou "Nenhum dado coletado" | API Omie fora do ar | Verificar credenciais Omie nos GitHub Secrets |
| Histórico duplicado | `historico_saldos` usa WRITE_APPEND | Usar view `v_historico_saldos` (dedup automático) |
| BP não processado | `BP.xlsx` não encontrada | Colocar planilha na raiz do repo |
| Sync lento (>30min) | Muitas chamadas API | Cache BQ de histórico reduz chamadas. Timeout: 40min |

---

## 10. Custos

| Item | Custo |
|------|-------|
| BigQuery storage (1 cliente) | ~R$ 0 (free tier) |
| BigQuery queries | ~R$ 0 (free tier: 1 TB/mês) |
| Cloud Functions | Grátis (2M invocações/mês) |
| GitHub Actions | Grátis (2000 min/mês) |
| **Total por cliente** | **~R$ 0/mês** |

Com 10+ clientes, pode sair do free tier (~R$ 5-20/mês por cliente adicional).

---

## 11. Próximos Passos

- [ ] Deploy da Cloud Function (`api_bq.py`)
- [ ] Hospedar `dashboard_bq.html` (GitHub Pages, Vercel, ou Cloud Storage)
- [ ] Fase 2: Agente WhatsApp (Claude API + WhatsApp Business API + BigQuery)
- [ ] Onboarding de novos clientes Omie
