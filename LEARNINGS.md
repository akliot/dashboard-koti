# Aprendizados — Dashboard Koti

> Documento vivo. Registre bugs, decisões e padrões aqui para que sessões futuras
> (humanas ou AI) não repitam erros. Formato: data, contexto curto, lição.

---

## 🔐 Segurança

### Cloud Function sem autenticação (31/03/2026)
**Problema:** A Cloud Function `api_dashboard` estava deployada com `--allow-unauthenticated` e **sem nenhum middleware de autenticação**. Qualquer pessoa com a URL acessava todos os dados financeiros (lançamentos, clientes, saldos bancários, orçamento DRE).
**Impacto:** 🔴 CRÍTICO — dados financeiros reais expostos na internet.
**Fix:** Adicionado middleware `_check_auth()` que valida header `X-API-Key` ou `Authorization: Bearer <key>` contra env var `DASHBOARD_API_KEY`. Sem key → 401.
**Regra:** TODA Cloud Function que serve dados sensíveis DEVE ter autenticação. Nunca confiar apenas em CORS (CORS protege browsers, não curl/scripts). Flag `--allow-unauthenticated` é mantida porque autenticação é no nível da aplicação (browser não tem IAM token), mas a API key no header é obrigatória.

### Senha client-side é cosmética (31/03/2026)
**Problema:** O login do dashboard validava a senha via PBKDF2 no browser (PASS_HASH hardcoded no HTML). Isso protege contra usuários leigos, mas a API era chamável diretamente sem senha.
**Fix:** A senha agora serve dupla função: (1) validação PBKDF2 de login (UX), (2) derivação de API key via PBKDF2 com salt diferente (segurança real). A API key NUNCA aparece no código — é gerada em runtime.
**Regra:** Autenticação client-side é UX, não segurança. Sempre validar no servidor.

### API key hardcoded em repo público (31/03/2026)
**Problema:** Na primeira iteração do fix, a API key foi colocada como constante no HTML (`const DASHBOARD_API_KEY = '...'`). Como o repo é público (GitHub Pages free exige repo público), a key ficava visível no source.
**Fix:** API key derivada da senha em runtime via PBKDF2(senha, salt_api, 100K). Key zero no código.
**Regra:** NUNCA hardcodar secrets em repos públicos. Se não pode tornar privado, derivar em runtime.

### PBKDF2 com poucas iterações (31/03/2026)
**Problema:** PASS_ITERATIONS era 10.000 — insuficiente para senhas curtas contra brute-force.
**Fix:** Aumentado para 100.000 iterações (login + API key).
**Regra:** PBKDF2 mínimo 100K iterações. OWASP recomenda 600K+ para SHA-256 em 2024.

### Dados pessoais no histórico git (31/03/2026)
**Problema:** `rh_data.json` (dados de folha de pagamento — LGPD) foi commitado em 5 commits e depois substituído por `rh_data.enc` (criptografado). Mas os dados em plaintext continuavam acessíveis no histórico git.
**Fix:** `git filter-repo --invert-paths --path rh_data.json` + force push.
**Regra:** Dados sensíveis commitados DEVEM ser removidos do histórico com `git filter-repo`, não basta apenas deletar o arquivo. Avisar todos os devs para re-clonar após force push.

### CORS não é segurança (31/03/2026)
**Problema:** A documentação mencionava "CORS restritivo" como medida de segurança. CORS é aplicado apenas por browsers — qualquer script/curl ignora completamente.
**Fix:** CORS mantido como boa prática, mas autenticação real (API key) adicionada.
**Regra:** CORS ≠ autenticação. CORS é controle de acesso browser-to-browser. Para proteger APIs, usar API keys, tokens JWT, ou OAuth.

### Cache-Control: public em dados sensíveis (31/03/2026)
**Problema:** A API retornava `Cache-Control: public, max-age=300` — permitindo que proxies/CDNs cacheassem dados financeiros.
**Fix:** Mudado para `Cache-Control: private, max-age=300`.
**Regra:** Dados sensíveis sempre "private". "Public" é para assets estáticos (CSS, JS, imagens).

---

## 🐛 Bugs e Armadilhas

### Template literals com ternário + CSS
**Problema:** `${val?'var(--green)':'var(--border)';font-size:11px}` — o `;` fecha a expressão dentro do `${}`.
**Fix:** Fechar o `}` antes do `;`: `${val?'var(--green)':'var(--border)'};font-size:11px`
**Regra:** Nunca colocar `;` de CSS dentro de `${}`.

### Canvas IDs duplicados
**Problema:** Abas diferentes usavam o mesmo `id` de canvas → Chart.js dava erro fatal.
**Fix:** Prefixar por aba (ex: `chartRec*`, `chartFin*`).
**Regra:** Canvas IDs devem ser ÚNICOS em toda a página.

---

## 📐 Decisões de Arquitetura

### Regime por aba
- **Caixa (abas 1, 2, 4):** `data_pagamento` para realizados, `data_previsao` para pendentes
- **Competência (abas 3, 5, 7, 8):** `data_vencimento`, exclui ATRASADO
- **Motivo:** Cada visão tem propósito diferente. Misturar regimes gera confusão.

### PBKDF2 com salt diferente para API key
- Login hash: `PBKDF2(senha, 'koti2026_salt_', 100K)` → validação client-side
- API key: `PBKDF2(senha, 'koti2026_api_key_salt', 100K)` → autenticação server-side
- **Motivo:** Mesmo hash não pode servir para login E autenticação. Salts diferentes = keys independentes derivadas da mesma senha.

### Faturamento Direto (FD) — ⚡ KOTI-SPECIFIC
- Entrada: categoria contém "Faturamento Direto" → FD
- Saída: `numero_documento` ou NF contém "fd" → FD
- Qualquer outro → SK (Studio Koti)

---

## 🔄 Padrões de Deploy

### Sequência correta
1. Validar sintaxe (Python + JS)
2. `python3 test_api.py -v` — testes unitários
3. `git add -A && git commit -m "tipo: msg" && git push`
4. Se alterou `api_bq.py` → redeploy Cloud Function
5. Se alterou `dashboard_bq.html` → GitHub Pages atualiza automaticamente
6. Verificar API: `curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: <key>" <URL>`

### Cloud Function deploy (com API key)
```bash
gcloud functions deploy api_dashboard \
  --gen2 --runtime python311 --trigger-http --allow-unauthenticated \
  --entry-point api_dashboard --source . \
  --set-env-vars GCP_PROJECT_ID=dashboard-koti-omie,BQ_DATASET=studio_koti,DASHBOARD_API_KEY=<KEY> \
  --region us-central1 --memory 512MB --timeout 60s
```

### Trocar senha do dashboard
```bash
python3 -c "
import hashlib, getpass
s = getpass.getpass('Nova senha: ')
print('PASS_HASH:', hashlib.pbkdf2_hmac('sha256', s.encode(), 'koti2026_salt_'.encode(), 100000).hex())
print('API_KEY:  ', hashlib.pbkdf2_hmac('sha256', s.encode(), 'koti2026_api_key_salt'.encode(), 100000).hex())
"
# 1. Substituir PASS_HASH no dashboard_bq.html
# 2. Redeploy Cloud Function com nova DASHBOARD_API_KEY
```

---

*Última atualização: 31/Mar/2026*
