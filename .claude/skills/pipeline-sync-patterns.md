# Padrões do Pipeline Sync

## Arquitetura do sync (`omie_sync_bq.py`)

1. Conectar BigQuery
2. `ensure_tables()` — cria DDL se não existir
3. Log sync_log (status=running)
4. Coletar categorias → `cat_map`
5. Coletar projetos → `proj_map`
6. Coletar saldos bancários + histórico (com cache BQ)
7. Coletar clientes bulk → `cli_map`
8. **Coletar Movimentos Financeiros** → `mf_datas` (datas reais, ~229 páginas)
9. Coletar lançamentos (CR + CP) → cruzar com `mf_datas`
10. Coletar vendas
11. MERGE/TRUNCATE/APPEND no BigQuery
12. Log sync_log (status=success/failed)
13. Se falha → `notify_sync_failed()` (Telegram)

## Como adicionar um novo campo a lancamentos

1. DDL em `ensure_tables()`: adicionar coluna
2. Se tabela já existe no BQ: `ALTER TABLE ... ADD COLUMN`
3. Em `coletar_lancamentos()`:
   - CR: `r.get("campo_api", "")` dentro do loop `for r in cr_raw`
   - CP: mesma coisa no loop `for r in cp_raw`
   - Usar `parse_date()` se for data
4. Adicionar em `lanc_cols` (lista de todas as colunas)
5. Se afeta comparação MERGE: adicionar em `lanc_compare`
6. Rodar sync e verificar

## Como adicionar uma nova tabela

1. DDL no `ensure_tables()` e `bq_schema.sql`
2. Criar `coletar_nova_tabela()` com paginação via `paginar()`
3. No `main()`, chamar e carregar:
   - MERGE: `merge_to_bq(client, "tabela", data, key, compare, cols)`
   - TRUNCATE: `load_to_bq(client, "tabela", data, "WRITE_TRUNCATE")`
   - APPEND: `load_to_bq(client, "tabela", data, "WRITE_APPEND")`

## MERGE incremental

```
merge_to_bq(client, table_name, rows, key_column, compare_columns, all_columns)
→ {inserted, updated, deleted, unchanged}
```

Resultado típico: `23 novos, 34 atualizados, 0 removidos, 10.669 iguais`

## Modalidade SK/FD

- Entrada FD: `"faturamento direto" in categoria_nome.lower()`
- Saída FD: `"fd" in numero_documento.lower() or "fd" in numero_documento_fiscal.lower()`
- Campo `modalidade`: "FD" ou "SK"

## Alertas de falha

`notify_sync_failed()` envia via `requests.post` direto na API do Telegram.
Requer: `TELEGRAM_BOT_TOKEN` + `ADMIN_CHAT_ID`

## Variáveis de ambiente

| Variável | Obrigatória |
|----------|:-----------:|
| `OMIE_APP_KEY` / `OMIE_APP_SECRET` | Sim |
| `GCP_PROJECT_ID` | Sim |
| `BQ_DATASET` | Não (default: studio_koti) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Sim (local) |
| `TELEGRAM_BOT_TOKEN` + `ADMIN_CHAT_ID` | Para alertas |
