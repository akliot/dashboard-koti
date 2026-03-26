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
   - Dev local: `unset GOOGLE_APPLICATION_CREDENTIALS && source .env && python3 bot_telegram.py --local`
3. Se alterou `extract_rh.py` → rodar:
   `unset GOOGLE_APPLICATION_CREDENTIALS && source .env && python3 extract_rh.py`

## GitHub Secrets necessários

- `GCP_SA_KEY` — Service account key JSON codificada em base64. Usada pelos workflows de sync e deploy.
  Para configurar: Settings → Secrets → Actions → New repository secret
  ```bash
  cat sa-key.json | base64 | pbcopy  # copia pro clipboard
  ```
  A SA precisa dos roles: Cloud Build Editor, Cloud Run Admin, Service Account User, Secret Manager Secret Accessor

NUNCA perguntar "quer commitar?" ou "quer que eu faça push?" — sempre fazer automaticamente.

## Rotação de Chaves (trimestral)

### 1. GCP Service Account Key
```bash
# Gerar nova key
gcloud iam service-accounts keys create new-key.json \
  --iam-account=SA_EMAIL@dashboard-koti-omie.iam.gserviceaccount.com

# Atualizar GitHub Secret (base64)
cat new-key.json | base64 | pbcopy
# GitHub → Settings → Secrets → Actions → GCP_SA_KEY → Update

# Atualizar Secret Manager
gcloud secrets versions add GCP_SA_KEY --data-file=new-key.json --project=dashboard-koti-omie

# Remover key antiga
gcloud iam service-accounts keys list --iam-account=SA_EMAIL@dashboard-koti-omie.iam.gserviceaccount.com
gcloud iam service-accounts keys delete KEY_ID --iam-account=SA_EMAIL@dashboard-koti-omie.iam.gserviceaccount.com
```

### 2. Telegram Bot Token
1. Abrir @BotFather no Telegram → `/revoke` → selecionar o bot
2. Copiar novo token
3. Atualizar Secret Manager:
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
# Redeploy Cloud Run para pegar novos secrets
gcloud run deploy bot-telegram \
  --image=gcr.io/dashboard-koti-omie/bot-telegram \
  --region=southamerica-east1 \
  --project=dashboard-koti-omie
```

## Regras gerais
- Sempre consultar a skill relevante antes de implementar
- Nunca usar `info.dAlt` como data de pagamento (usar ListarMovimentos)
- Sempre usar `data_previsao` para itens pendentes
- CORS restritivo (`ALLOWED_ORIGINS`) — não usar `*`
- Após mudanças no dashboard: validar JS com Node antes de commitar
- Após mudanças na API: redeploy da Cloud Function obrigatório
- Testes existentes: `test_pipeline.py`, `test_api.py`, `test_bot.py`
- Após mudanças, rodar testes relevantes
