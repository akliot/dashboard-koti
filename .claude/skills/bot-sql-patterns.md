# Padrões do Bot Telegram

## Arquitetura

Pergunta → Bot Telegram → LLM gera SQL → BigQuery executa → LLM formata → resposta

## LLM Providers

Auto-detect por env var:
- `ANTHROPIC_API_KEY` → **Claude Haiku 4.5** (preferido)
- `GEMINI_API_KEY` → **Gemini 2.5 Flash** (fallback)
- Retry com backoff em 429

## Regras de negócio no prompt SQL

1. "faturei", "faturamento", "NF" → entradas RECEBIDO, filtrar por `data_pagamento`
2. "paguei", "pagamentos" → saídas PAGO, filtrar por `data_pagamento`
3. "previstos", "a receber", "a pagar", "pagar hoje" → status IN ('A VENCER','ATRASADO','VENCE HOJE'), filtrar por `data_previsao`
4. "recebimentos" sem qualificador → entradas RECEBIDO
5. Mês sem ano → assumir ano corrente
6. Busca de nomes → `LOWER(campo) LIKE LOWER('%termo%')` (BQ é case-sensitive!)
7. Follow-up → copiar filtros de data do SQL anterior
8. Nomes de empresas → `cliente_nome` (nunca `categoria_nome`)
9. "mão de obra", "marcenaria" → `categoria_nome`

## Memória de conversa

- `chat_history` dict por `chat_id`
- Últimas 5 trocas (incluindo SQL gerado)
- Follow-ups como "desse"/"disso" mantêm contexto

## Desambiguação

- 200+ stopwords financeiras filtradas
- Se 0 resultados e tem palavras não-stopword: sugere nomes similares
- Fragmentos de 4 letras para typos ("castini" → "cast","asti","stin","tini")

## BigQuery SQL — cuidados

- `STRING_AGG` em vez de `GROUP_CONCAT`
- `EXTRACT(MONTH FROM campo)` em vez de `MONTH(campo)`
- `FORMAT_DATE('%Y-%m', campo)` para agrupamento mensal
- `LIMIT 20` obrigatório
- Timeout: 15s por query

## Como adicionar novo comando

```python
async def cmd_novo(update, context):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    # lógica
    await update.message.reply_text(resposta)

# Registrar:
app.add_handler(CommandHandler("novo", cmd_novo))
```

## /analise

`financial_snapshot()` roda 8 queries simultâneas:
- Resumo mensal, saldos, a receber/pagar, top despesas, top clientes, margem projetos, orçamento
- LLM analisa como consultor financeiro

## Segurança

- `AUTHORIZED_CHAT_IDS`: IDs autorizados (comma-separated)
- SQL safety: só SELECT, só dataset autorizado, palavras perigosas bloqueadas
- Respostas truncadas em 4000 chars (limite Telegram)

## Testes

`test_bot.py`: 54 cenários em 17 grupos (91% pass com Claude Haiku)
```bash
export $(cat .env | grep -v '^#' | xargs) && python3 test_bot.py
```
