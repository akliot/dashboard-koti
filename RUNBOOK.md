# Runbook — Dashboard Koti

## 1. Sync falhou (Omie → BigQuery)

**Sintoma:** Dashboard sem dados novos, sync_log mostra `failed`.

**Diagnóstico:**
```bash
# Ver últimos runs no GitHub Actions
gh run list --workflow=sync_omie_bq.yml --limit=5

# Ver logs do run que falhou
gh run view <RUN_ID> --log-failed
```

**Resolução:**
1. Re-rodar manualmente: GitHub → Actions → Sync Omie → BigQuery → Run workflow
2. Se erro de autenticação: verificar se os secrets `OMIE_APP_KEY` e `OMIE_APP_SECRET` estão válidos em Settings → Secrets
3. Se erro de quota/timeout: aguardar e re-rodar (a API Omie tem rate limit)
4. Se erro de BQ: verificar se a SA tem permissão `BigQuery Data Editor`

---

## 2. Bot parou de responder

**Sintoma:** Mensagens no Telegram sem resposta.

**Diagnóstico:**
```bash
# Ver logs do Cloud Run
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=bot-telegram" \
  --limit=30 --format="value(textPayload)" --project=dashboard-koti-omie

# Ver status do serviço
gcloud run services describe bot-telegram --region=southamerica-east1 --project=dashboard-koti-omie
```

**Resolução:**
1. Forçar nova revisão (redeploy):
```bash
gcloud builds submit --tag gcr.io/dashboard-koti-omie/bot-telegram --project=dashboard-koti-omie
gcloud run deploy bot-telegram \
  --image=gcr.io/dashboard-koti-omie/bot-telegram \
  --region=southamerica-east1 \
  --project=dashboard-koti-omie
```
2. Se erro de secret: verificar se `TELEGRAM_BOT_TOKEN` e `ANTHROPIC_API_KEY` existem no Secret Manager
3. Se webhook desconfigurado: o bot re-registra automaticamente ao iniciar
4. Dev local (emergência): `source .env && python3 bot_telegram.py --local`

---

## 3. Dashboard sem dados

**Sintoma:** Dashboard carrega mas mostra zeros ou "sem dados".

**Diagnóstico:**
```sql
-- No BigQuery, verificar último sync
SELECT status, started_at, finished_at, duration_seconds
FROM `dashboard-koti-omie.studio_koti.sync_log`
ORDER BY started_at DESC LIMIT 5;

-- Verificar se há lançamentos recentes
SELECT COUNT(*) as total, MAX(data_emissao) as ultimo
FROM `dashboard-koti-omie.studio_koti.lancamentos`;
```

**Resolução:**
1. Se sync_log mostra `failed`: ver item 1
2. Se sync_log mostra `success` mas sem dados: problema na API Omie — verificar manualmente no painel Omie
3. Se dados existem no BQ mas dashboard não mostra: verificar a Cloud Function `api_dashboard` e CORS

---

## 4. Dados inconsistentes (BQ vs Omie)

**Sintoma:** Valores no dashboard diferem do Omie.

**Diagnóstico:**
1. Identificar o lançamento divergente (número do documento)
2. Comparar no BQ:
```sql
SELECT * FROM `dashboard-koti-omie.studio_koti.lancamentos`
WHERE numero_documento = 'XXXX';
```
3. Comparar com o Omie (Financeiro → Contas a Pagar/Receber)

**Resolução:**
1. Se o dado no BQ está desatualizado: re-rodar sync (GitHub Actions → Run workflow)
2. Se o sync não corrige: verificar se o campo `data_pagamento` está sendo populado via ListarMovimentos (nunca usar `info.dAlt`)
3. Sync faz WRITE_TRUNCATE — re-rodar sempre traz snapshot completo atualizado

---

## 5. Novo acesso ao bot

**Sintoma:** Usuário envia mensagem e recebe "Acesso não autorizado".

**Como pegar o chat_id:**
```bash
# Nos logs do Cloud Run, procurar a tentativa negada
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=bot-telegram AND textPayload:\"Acesso negado\"" \
  --limit=5 --format="value(textPayload)" --project=dashboard-koti-omie
```
O log mostra: `Acesso negado para chat_id=XXXXXXXX`

**Adicionar acesso:**
1. Editar `AUTHORIZED_CHAT_IDS` no Cloud Run:
```bash
gcloud run services update bot-telegram \
  --region=southamerica-east1 \
  --update-env-vars="^||^AUTHORIZED_CHAT_IDS=8107744840,8230872349,NOVO_ID" \
  --project=dashboard-koti-omie
```
2. Atualizar também no `deploy_bot.yml` para persistir em futuros deploys

---

## 6. Novo exec (acesso à folha/saldos)

**Sintoma:** Usuário autorizado mas recebe "Acesso restrito à diretoria" ao perguntar sobre folha ou saldos.

**Adicionar como exec:**
1. Editar `EXEC_CHAT_IDS` no Cloud Run:
```bash
gcloud run services update bot-telegram \
  --region=southamerica-east1 \
  --set-env-vars="EXEC_CHAT_IDS=8230872349,NOVO_ID" \
  --project=dashboard-koti-omie
```
2. Atualizar também no `deploy_bot.yml` para persistir em futuros deploys
3. O `ADMIN_CHAT_ID` (8107744840) é automaticamente exec — não precisa estar no `EXEC_CHAT_IDS`
