# Runbook — Dashboard Koti

## 1. Sync falhou (Omie → BigQuery)

**Sintoma:** Dashboard sem dados novos, sync_log mostra `failed`.

**Diagnóstico:**
```bash
gh run list --workflow=sync_omie_bq.yml --limit=5
gh run view <RUN_ID> --log-failed
```

**Resolução:**
1. Re-rodar: GitHub → Actions → Sync Omie → BigQuery → Run workflow
2. Erro de autenticação: verificar secrets `OMIE_APP_KEY`, `OMIE_APP_SECRET` em Settings → Secrets
3. Erro de quota/timeout: aguardar e re-rodar (API Omie tem rate limit, retry automático 2x)
4. Erro de BQ: verificar se a SA tem `BigQuery Data Editor`
5. Erro de DRE_MAP (>3 labels mismatch): planilha BP mudou de estrutura → atualizar `DRE_MAP` em `extract_bp_bq.py`

---

## 2. Bot parou de responder

**Sintoma:** Mensagens no Telegram sem resposta.

**Diagnóstico:**
```bash
# Logs do Cloud Run
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=bot-telegram" \
  --limit=30 --format="value(textPayload)" --project=dashboard-koti-omie

# Status do serviço
gcloud run services describe bot-telegram --region=southamerica-east1 --project=dashboard-koti-omie
```

**Resolução:**
1. Redeploy:
```bash
gcloud builds submit --tag gcr.io/dashboard-koti-omie/bot-telegram --project=dashboard-koti-omie
gcloud run deploy bot-telegram --image=gcr.io/dashboard-koti-omie/bot-telegram \
  --region=southamerica-east1 --project=dashboard-koti-omie
```
2. Erro de secret: verificar `TELEGRAM_BOT_TOKEN` e `ANTHROPIC_API_KEY` no Secret Manager:
```bash
gcloud secrets list --project=dashboard-koti-omie
gcloud secrets versions access latest --secret=TELEGRAM_BOT_TOKEN --project=dashboard-koti-omie | head -c5
```
3. Webhook desconfigurado: o bot re-registra automaticamente ao iniciar (auto-discover via K_SERVICE)
4. Emergência local: `source .env && python3 bot_telegram.py --local`
5. Deploy automático falhou: verificar GitHub Actions → Deploy Bot → Cloud Run

---

## 3. Dashboard sem dados

**Sintoma:** Dashboard carrega mas mostra zeros ou "sem dados".

**Diagnóstico:**
```sql
-- Último sync
SELECT status, started_at, finished_at, duration_seconds
FROM `dashboard-koti-omie.studio_koti.sync_log`
ORDER BY started_at DESC LIMIT 5;

-- Lançamentos recentes
SELECT COUNT(*) as total, MAX(data_emissao) as ultimo
FROM `dashboard-koti-omie.studio_koti.lancamentos`;
```

**Resolução:**
1. sync_log `failed`: ver item 1
2. sync_log `success` mas sem dados: problema na API Omie — verificar painel Omie
3. Dados existem no BQ mas dashboard vazio: verificar Cloud Function `api_dashboard` e CORS
4. Dashboard RH vazio: verificar se `rh_data.enc` existe e está atualizado — rodar `extract_rh.py`
5. Visão Geral headcount "--": `rh_data.enc` não está acessível ou decriptação falhou

---

## 4. Dados inconsistentes (BQ vs Omie)

**Sintoma:** Valores no dashboard diferem do Omie.

**Diagnóstico:**
```sql
SELECT * FROM `dashboard-koti-omie.studio_koti.lancamentos`
WHERE numero_documento = 'XXXX';
```
Comparar com Omie → Financeiro → Contas a Pagar/Receber.

**Resolução:**
1. Re-rodar sync (WRITE_TRUNCATE = snapshot completo)
2. Se persiste: verificar `data_pagamento` via ListarMovimentos (nunca `info.dAlt`)
3. Folha inconsistente: verificar se `extract_rh.py` está filtrando subtotais corretamente
4. Custo total errado: `custo_total` = coluna 24 da planilha − rescisão (PJ sem encargos, CLT com)
5. Transferências: verificado que não há categorias de transferência nos dados (CONTAS_IGNORAR filtra contas fictícias)

---

## 5. Novo acesso ao bot

**Sintoma:** Usuário recebe "Acesso não autorizado".

**Pegar chat_id:**
```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=bot-telegram AND textPayload:\"Acesso negado\"" \
  --limit=5 --format="value(textPayload)" --project=dashboard-koti-omie
```

**Adicionar acesso:**
```bash
gcloud run services update bot-telegram --region=southamerica-east1 \
  --update-env-vars="^||^AUTHORIZED_CHAT_IDS=8107744840,8230872349,NOVO_ID" \
  --project=dashboard-koti-omie
```
Atualizar também em `.github/workflows/deploy_bot.yml` para persistir.

---

## 6. Novo exec (acesso à folha/saldos)

**Sintoma:** Usuário autorizado recebe "Acesso restrito à diretoria".

**Adicionar como exec:**
```bash
gcloud run services update bot-telegram --region=southamerica-east1 \
  --set-env-vars="EXEC_CHAT_IDS=8230872349,NOVO_ID" \
  --project=dashboard-koti-omie
```
Atualizar em `deploy_bot.yml`. O `ADMIN_CHAT_ID` (8107744840) é automaticamente exec.

---

## 7. Atualizar dados RH

**Quando:** Nova planilha de folha disponível.

**Procedimento:**
1. Colocar `Folha de Pagamentos 2026.xlsx` em `~/Downloads/`
2. Rodar:
```bash
unset GOOGLE_APPLICATION_CREDENTIALS && source .env && python3 extract_rh.py
```
3. Commit e push do `rh_data.enc`:
```bash
git add rh_data.enc && git commit -m "data: update rh_data.enc" && git push
```
4. `rh_data.json` é local only (`.gitignore`) — nunca commitar

---

## 8. Alterar senha do dashboard

**Procedimento:**
1. Gerar novo hash PBKDF2 (login):
```python
import hashlib
dk = hashlib.pbkdf2_hmac('sha256', b'NOVA_SENHA', b'koti2026_salt_', 10000)
print(dk.hex())
```
2. Atualizar `PASS_HASH` em `dashboard_bq.html`
3. Re-encriptar `rh_data.enc` com nova senha (alterar no `extract_rh.py` e rodar)
4. Atualizar senha hardcoded em `dashboard_rh.html` (`decryptRH` call)
5. Atualizar senha no `dashboard_bq.html` (`decryptFile` call para headcount na Visão Geral)

---

## 9. Bot mostrando departamento em vez de nome

**Sintoma:** Bot retorna nome de departamento (COMERCIAL, ARQUITETURA) em vez do funcionário.

**Causa:** LLM gerou SQL com GROUP BY departamento ou sem campo nome.

**Resolução:**
1. Verificar se `extract_rh.py` está filtrando subtotais de departamento:
```bash
source .env && python3 extract_rh.py
```
2. O `bot_telegram.py` já tem pós-processamento que injeta `nome` e remove GROUP BY
3. Se persistir: reforçar exemplos no SQL_SYSTEM_PROMPT do bot

---

## 10. Bot calculando encargos fictícios

**Sintoma:** Bot mostra custo_total muito acima de salário + benefícios, mencionando INSS/FGTS.

**Causa:** LLM inventa encargos para justificar o valor do `custo_total`.

**Resolução:**
1. Verificar se `custo_total` no BQ está correto: `= coluna 24 planilha - rescisão`
2. O FORMAT_PROMPT já proíbe menção de encargos (empresa PJ)
3. Se persistir: verificar se a planilha não mudou o cálculo da coluna 24
