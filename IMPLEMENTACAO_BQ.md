# Implementação: Migração Pipeline Omie → BigQuery

**Data**: 19/03/2026
**Status**: Arquivos criados e validados localmente — aguardando setup GCP e push

---

## 1. O que foi feito

Criação de 5 novos arquivos no repositório `dashboard-koti` para migrar o pipeline financeiro do Studio Koti de GitHub Pages (JSON) para BigQuery + Looker Studio.

O pipeline legado (GitHub Pages) **não foi alterado** — os novos scripts rodam em paralelo.

### Arquivos criados

| Arquivo | Linhas | Descrição |
|---------|:------:|-----------|
| `omie_sync_bq.py` | ~430 | Script principal — coleta dados da API Omie e escreve em 8 tabelas BigQuery |
| `extract_bp_bq.py` | ~140 | Extrai DRE Real vs Orçado da planilha BP Excel → tabela `orcamento_dre` |
| `bq_schema.sql` | ~150 | DDL com 9 `CREATE TABLE` + 1 `CREATE VIEW` para execução no BigQuery Console |
| `.github/workflows/sync_omie_bq.yml` | ~70 | Workflow GitHub Actions (BQ + legacy em paralelo) |
| `requirements_bq.txt` | 4 | Dependências Python: requests, google-cloud-bigquery, openpyxl, db-dtypes |

### Arquivos existentes NÃO modificados

- `omie_sync.py` (pipeline legado)
- `extract_orcamento.py` (pipeline legado)
- `encrypt_data.py`
- `dashboard_omie.html`
- `index.html`
- `.github/workflows/sync_omie.yml` (workflow legado)

---

## 2. Tabelas BigQuery

### Schema: `{GCP_PROJECT_ID}.studio_koti`

| # | Tabela | Registros (estimado) | Estratégia | Partição |
|:-:|--------|:--------------------:|:----------:|----------|
| 1 | `lancamentos` | ~12.000 | WRITE_TRUNCATE | `sync_date` |
| 2 | `saldos_bancarios` | ~13 | WRITE_TRUNCATE | `sync_date` |
| 3 | `historico_saldos` | ~200+ (acumula) | WRITE_APPEND | `sync_date` |
| 4 | `categorias` | ~142 | WRITE_TRUNCATE | — |
| 5 | `projetos` | ~213 | WRITE_TRUNCATE | — |
| 6 | `clientes` | ~1.830 | WRITE_TRUNCATE | — |
| 7 | `vendas_pedidos` | variável | WRITE_TRUNCATE | `sync_date` |
| 8 | `orcamento_dre` | ~26 × 12 = ~312 | WRITE_TRUNCATE | — |
| 9 | `sync_log` | 1/dia (acumula) | WRITE_APPEND | — |

### View

| View | Descrição |
|------|-----------|
| `v_historico_saldos` | Deduplicação automática do `historico_saldos` por `(conta_id, data_referencia, tipo)` — usar no Looker Studio |

---

## 3. Lógica implementada

### `omie_sync_bq.py`

1. Autenticação via `GOOGLE_APPLICATION_CREDENTIALS` (service account JSON)
2. Registro de sync no `sync_log` (status=running)
3. Coleta da API Omie (mesma lógica do `omie_sync.py` v6):
   - `ListarCategorias` + `ConsultarCategoria` (faltantes)
   - `ListarProjetos`
   - `ListarExtrato` (saldos D-1 + histórico mensal/diário)
   - `ListarContasReceber` + `ListarContasPagar`
   - `ListarClientes` (bulk)
   - `ListarPedidos` (1 linha por item)
4. Transformação para schema BigQuery
5. Carga via `load_table_from_json`
6. Registro de sucesso/falha no `sync_log`

**Regras Koti-specific** (marcadas com `# ⚡ KOTI-SPECIFIC`):
- `CONTAS_IGNORAR = {8754849088}` — conta fictícia BAIXA DE NFS
- `is_faturamento_direto`:
  - Entrada: `True` se `categoria_nome` contém "Faturamento Direto"
  - Saída: `True` se `numero_documento` contém "FD" (case-insensitive)

### `extract_bp_bq.py`

1. Busca planilha `BP.xlsx` ou `BP*.xlsx` no diretório do script
2. Lê abas "Realizado" e "BP" com openpyxl
3. Detecta meses com dados reais (Receita Bruta ≠ 0)
4. Flatten: 1 linha por item DRE × mês (26 linhas × 12 meses = 312 registros)
5. Calcula `variacao_pct = (real - bp) / abs(bp) * 100` (NULL se bp=0)
6. Carrega em `orcamento_dre` com WRITE_TRUNCATE

### GitHub Actions (`sync_omie_bq.yml`)

- Roda diariamente às 5h BRT (8h UTC) + manual dispatch
- Pipeline BQ roda primeiro (com retry: 2 tentativas, 60s entre elas)
- Pipeline legado roda em seguida (commit + push dos `.enc`)
- Cleanup do `gcp-key.json` temporário (sempre)

---

## 4. Variáveis de ambiente

| Variável | Onde configurar | Usado por |
|----------|----------------|-----------|
| `OMIE_APP_KEY` | GitHub Secret (já existe) | `omie_sync_bq.py` |
| `OMIE_APP_SECRET` | GitHub Secret (já existe) | `omie_sync_bq.py` |
| `GCP_PROJECT_ID` | GitHub Secret (novo) | ambos scripts |
| `GCP_SA_KEY` | GitHub Secret (novo) | workflow (decodifica → JSON) |
| `BQ_DATASET` | Hardcoded no workflow: `studio_koti` | ambos scripts |
| `GOOGLE_APPLICATION_CREDENTIALS` | Setado pelo workflow | ambos scripts |

---

## 5. Validações realizadas

| # | Validação | Resultado |
|:-:|-----------|:---------:|
| 1 | Syntax check `omie_sync_bq.py` | ✅ OK |
| 2 | Syntax check `extract_bp_bq.py` | ✅ OK |
| 3 | Scripts legados não modificados | ✅ OK (zero diff) |
| 4 | `bq_schema.sql`: 9 CREATE TABLE + 1 VIEW | ✅ OK |
| 5 | 5 arquivos novos criados | ✅ OK |

---

## 6. Próximos passos (manuais)

### Fase 1: Infraestrutura GCP
- [ ] Criar projeto GCP (`dashboard-omie` ou outro nome)
- [ ] Habilitar BigQuery API (`gcloud services enable bigquery.googleapis.com`)
- [ ] Criar service account `omie-sync` com roles `bigquery.dataEditor` + `bigquery.jobUser`
- [ ] Gerar chave JSON da service account
- [ ] Adicionar GitHub Secrets: `GCP_PROJECT_ID` e `GCP_SA_KEY` (JSON base64)
- [ ] Criar dataset: `bq mk --dataset --location=US {PROJECT}:studio_koti`
- [ ] Executar `bq_schema.sql` no BigQuery Console (cria tabelas + view)

### Fase 2: Deploy
- [ ] Fazer `git push` dos novos arquivos
- [ ] Rodar workflow manualmente (Actions → "Sync Omie → BigQuery" → Run workflow)
- [ ] Verificar `sync_log` no BigQuery Console
- [ ] Verificar dados nas tabelas

### Fase 3: Looker Studio
- [ ] Conectar fonte de dados BigQuery no Looker Studio
- [ ] Criar páginas do dashboard (Visão Geral, Fluxo de Caixa, Financeiro, Conciliação, Vendas, Projetos, Real vs Orçado)
- [ ] Configurar filtros globais (período, projeto, tipo)

### Fase 4: Desativação do legado (quando confiante)
- [ ] Validar dados BQ vs GitHub Pages
- [ ] Remover steps legados do workflow
- [ ] Remover arquivos legados do repo

---

## 7. Referências

| Documento | Localização |
|-----------|-------------|
| Documentação técnica completa | `~/Library/CloudStorage/GoogleDrive-akliot@gmail.com/My Drive/STUDIO KOTI/Dashboard/engenharia_dados_bigquery.md` |
| Config Koti (contas, DRE_MAP, volumes) | `~/Library/CloudStorage/GoogleDrive-akliot@gmail.com/My Drive/STUDIO KOTI/Dashboard/Contexto/projeto-koti.md` |
| Repositório GitHub | https://github.com/akliot/dashboard-koti |
