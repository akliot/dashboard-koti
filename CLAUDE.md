# Dashboard Koti — Claude Code Reference

## Skills disponíveis
- `.claude/skills/omie-api-reference.md` — Referência completa da API Omie
- `.claude/skills/dashboard-patterns.md` — Padrões do dashboard HTML
- `.claude/skills/pipeline-sync-patterns.md` — Padrões do pipeline sync
- `.claude/skills/bot-sql-patterns.md` — Padrões do bot Telegram

## Auto-deploy (NÃO pedir confirmação)

Após qualquer alteração:
1. `git add -A && git commit -m "tipo: descrição" && git push`
2. Se alterou `bot_telegram.py`, `Dockerfile` ou `requirements_bot.txt`:
   - Cloud Run: deploy automático via GitHub Actions (`.github/workflows/deploy_bot.yml`)
   - Deploy manual: `gcloud builds submit --tag gcr.io/dashboard-koti-omie/bot-telegram && gcloud run deploy bot-telegram --image=gcr.io/dashboard-koti-omie/bot-telegram --region=southamerica-east1 --project=dashboard-koti-omie`
   - Dev local: `unset GOOGLE_APPLICATION_CREDENTIALS && source .env && python3 bot_telegram.py --local`
3. Se alterou `extract_rh.py` → rodar:
   `unset GOOGLE_APPLICATION_CREDENTIALS && source .env && python3 extract_rh.py`
   (gera `rh_data.json` local + `rh_data.enc` encriptado + upload BQ)
4. Se alterou `dashboard_bq.html` ou `dashboard_rh.html` → deploy via GitHub Pages (push para main)

NUNCA perguntar "quer commitar?" ou "quer que eu faça push?" — sempre fazer automaticamente.

## Arquivos removidos (legacy)
Estes arquivos foram deletados — não recriar:
- `omie_sync.py` — substituído por `omie_sync_bq.py`
- `encrypt_data.py` — encriptação agora inline no `extract_rh.py`
- `extract_orcamento.py` — substituído por `extract_bp_bq.py`
- `dashboard_omie.html` — substituído por `dashboard_bq.html`
- `sync_omie.yml` — substituído por `sync_omie_bq.yml`

## Serviços e URLs
- **Dashboard**: https://akliot.github.io/dashboard-koti/dashboard_bq.html
- **Bot Cloud Run**: https://bot-telegram-294770561801.southamerica-east1.run.app
- **Projeto GCP**: dashboard-koti-omie
- **Repositório**: https://github.com/akliot/dashboard-koti

## Bot Telegram
- **Modos**: `--local` (polling, dev), `--webhook` (Cloud Run, prod), `--cli` (terminal)
- **LLM**: Claude Haiku 4.5 via Anthropic SDK
- **RBAC**: `AUTHORIZED_CHAT_IDS` (acesso geral), `EXEC_CHAT_IDS` (folha/saldos), `ADMIN_CHAT_ID` (auto-exec)
- **Segurança**: rate-limit 10/min, SQL read-only, tabelas restritas para não-exec
- **Webhook**: auto-descobre URL via K_SERVICE, secret token derivado do bot token (SHA256)

## Criptografia
- **Login dashboard**: PBKDF2 + SHA-256 com salt `koti2026_salt_`, 10.000 iterações. Senha: `koti2025`
- **rh_data.enc**: AES-256-GCM + PBKDF2 com 100.000 iterações, salt aleatório. Mesma senha `koti2025`
- `rh_data.json` está no `.gitignore` — nunca commitar em texto plano

## Folha de pagamento (RH)
- Maioria dos funcionários é **PJ** — encargos trabalhistas NÃO existem
- Exceção: Auxiliar de Limpeza é **CLT** (com encargos)
- `custo_total` = coluna 24 da planilha (já tem encargos CLT corretos) − rescisão
- Rescisão é evento pontual, fica no campo `rescisao` separado
- `extract_rh.py` filtra subtotais de departamento (linhas sem cargo ou ALL CAPS)

## GitHub Secrets necessários
- `GCP_SA_KEY` — Service account key JSON codificada em base64
  ```bash
  cat sa-key.json | base64 | pbcopy  # copia pro clipboard
  ```
  Roles necessários: Cloud Build Editor, Cloud Run Admin, Service Account User, Secret Manager Secret Accessor
- `OMIE_APP_KEY` / `OMIE_APP_SECRET` — Credenciais da API Omie
- `GCP_PROJECT_ID` — `dashboard-koti-omie`

## GCP Secret Manager
- `TELEGRAM_BOT_TOKEN` — Token do bot Telegram
- `ANTHROPIC_API_KEY` — API key Anthropic (Claude Haiku)

## Rotação de Chaves (trimestral)

### 1. GCP Service Account Key
```bash
gcloud iam service-accounts keys create new-key.json \
  --iam-account=SA_EMAIL@dashboard-koti-omie.iam.gserviceaccount.com
cat new-key.json | base64 | pbcopy
# GitHub → Settings → Secrets → Actions → GCP_SA_KEY → Update
gcloud iam service-accounts keys list --iam-account=SA_EMAIL@dashboard-koti-omie.iam.gserviceaccount.com
gcloud iam service-accounts keys delete OLD_KEY_ID --iam-account=SA_EMAIL@dashboard-koti-omie.iam.gserviceaccount.com
```

### 2. Telegram Bot Token
1. @BotFather → `/revoke` → selecionar bot
2. Atualizar Secret Manager:
   ```bash
   printf '%s' "NOVO_TOKEN" | gcloud secrets versions add TELEGRAM_BOT_TOKEN --data-file=- --project=dashboard-koti-omie
   ```

### 3. Anthropic API Key
1. Regenerar em https://console.anthropic.com/settings/keys
2. Atualizar Secret Manager:
   ```bash
   printf '%s' "NOVA_KEY" | gcloud secrets versions add ANTHROPIC_API_KEY --data-file=- --project=dashboard-koti-omie
   ```

### Após qualquer rotação
```bash
gcloud run deploy bot-telegram --image=gcr.io/dashboard-koti-omie/bot-telegram \
  --region=southamerica-east1 --project=dashboard-koti-omie
```

## Validações
- `extract_bp_bq.py`: valida DRE_MAP contra labels da planilha BP. >3 mismatches = abort
- `bot_telegram.py`: injeta `nome` em queries de folha se LLM omitir, remove GROUP BY indevido
- Dashboard: badges de regime contábil (caixa/competência) por aba
- Testes: `test_pipeline.py`, `test_api.py`, `test_bot.py`

## Regras gerais
- Nunca usar `info.dAlt` como data de pagamento (usar ListarMovimentos)
- Sempre usar `data_previsao` para itens pendentes
- CORS restritivo (`ALLOWED_ORIGINS`) — não usar `*`
- Após mudanças no dashboard: validar JS com Node antes de commitar
- Após mudanças na API: redeploy da Cloud Function obrigatório
