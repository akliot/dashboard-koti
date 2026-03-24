# Dashboard Koti — Claude Code Reference

## Skills disponíveis
- `.claude/skills/omie-api-reference.md` — Referência completa da API Omie
- `.claude/skills/dashboard-patterns.md` — Padrões do dashboard HTML
- `.claude/skills/pipeline-sync-patterns.md` — Padrões do pipeline sync
- `.claude/skills/bot-sql-patterns.md` — Padrões do bot Telegram

## Auto-deploy (NÃO pedir confirmação)

Após qualquer alteração:
1. `git add -A && git commit -m "tipo: descrição" && git push`
2. Se alterou `bot_telegram.py` → reiniciar bot:
   `unset GOOGLE_APPLICATION_CREDENTIALS && source .env && python3 bot_telegram.py --local`
3. Se alterou `extract_rh.py` → rodar:
   `unset GOOGLE_APPLICATION_CREDENTIALS && source .env && python3 extract_rh.py`

NUNCA perguntar "quer commitar?" ou "quer que eu faça push?" — sempre fazer automaticamente.

## Regras gerais
- Sempre consultar a skill relevante antes de implementar
- Nunca usar `info.dAlt` como data de pagamento (usar ListarMovimentos)
- Sempre usar `data_previsao` para itens pendentes
- CORS restritivo (`ALLOWED_ORIGINS`) — não usar `*`
- Após mudanças no dashboard: validar JS com Node antes de commitar
- Após mudanças na API: redeploy da Cloud Function obrigatório
- Testes existentes: `test_pipeline.py`, `test_api.py`, `test_bot.py`
- Após mudanças, rodar testes relevantes
