# Referência API Omie

## Endpoints ativos

| Endpoint | Call | Dados | Paginação |
|----------|------|-------|-----------|
| geral/categorias | ListarCategorias | Categorias contábeis | Sim |
| geral/projetos | ListarProjetos | Projetos/obras | Sim |
| geral/contacorrente | ListarContasCorrentes | Contas bancárias | Sim |
| financas/extrato | ListarExtrato | Saldos e conciliação | Por conta |
| financas/contareceber | ListarContasReceber | Contas a receber | Sim |
| financas/contapagar | ListarContasPagar | Contas a pagar | Sim |
| **financas/mf** | **ListarMovimentos** | **Datas reais de pagamento** | Sim (229 pags) |
| geral/clientes | ListarClientes | Clientes/fornecedores | Sim (bulk) |
| produtos/pedido | ListarPedidos | Pedidos de venda | Sim |

## Padrão de chamada

POST `https://app.omie.com.br/api/v1/{endpoint}/`
```json
{"call": "{Call}", "app_key": "...", "app_secret": "...", "param": [{...}]}
```
Paginação: `nPagina`, `nRegPorPagina` (max 500), response: `nTotPaginas`

## Rate limits e retry

- `time.sleep(0.05)` entre chamadas individuais
- `time.sleep(0.1)` para ListarMovimentos (volume alto)
- Retry com backoff: 2s × tentativa
- Timeout: 60s por request
- Se "Já existe uma requisição": esperar e tentar de novo

## Campos críticos — LIÇÕES APRENDIDAS

**NUNCA usar `info.dAlt` como data de pagamento:**
- `info.dAlt` = data da ÚLTIMA ALTERAÇÃO do registro (não é a data do pagamento)
- Tipicamente 1 dia DEPOIS do pagamento real
- Causou erro de 460% nos valores de março

**SEMPRE usar ListarMovimentos (financas/mf) para data real:**
- `detalhes.nCodTitulo` = `codigo_lancamento_omie` do CR/CP (link direto por ID)
- `detalhes.dDtPagamento` = data REAL do pagamento/recebimento
- Match: 99.99% (9.055/9.056 lançamentos)

**nCodLancRelac do extrato NÃO bate com codigo_lancamento_omie** — IDs incompatíveis.

## Mapeamento de campos CR/CP

| Campo no JSON | Campo no BQ | Notas |
|---------------|-------------|-------|
| codigo_lancamento_omie | id | Primary key, INT64 |
| valor_documento | valor | FLOAT64 |
| status_titulo | status | UPPER: RECEBIDO, PAGO, A VENCER, ATRASADO, VENCE HOJE |
| data_vencimento | data_vencimento | DD/MM/YYYY → DATE |
| data_emissao | data_emissao | DD/MM/YYYY → DATE |
| data_previsao | data_previsao | DD/MM/YYYY → DATE, fallback: data_vencimento |
| MF.dDtPagamento | data_pagamento | Data real via ListarMovimentos |
| codigo_categoria | categoria_codigo | STRING |
| codigo_projeto | projeto_id | INT64, resolve nome via proj_map |
| codigo_cliente_fornecedor | cliente_id | INT64, resolve nome via cli_map |
| numero_documento | numero_documento | STRING |
| numero_documento_fiscal | — | Usado para detectar FD em saídas |

## Contas ignoradas

`CONTAS_IGNORAR = {8754849088}` — "BAIXA DE NFS" (conta fictícia Koti)

## Formatos de data

API retorna DD/MM/YYYY. `parse_date()` converte para YYYY-MM-DD (ISO) para BigQuery.
