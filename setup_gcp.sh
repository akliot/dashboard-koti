#!/bin/bash
# ============================================================
# Setup GCP para Dashboard Koti — BigQuery
# Execute no MESMO terminal onde fez gcloud auth login
# ============================================================
set -e

GCLOUD=~/google-cloud-sdk/bin/gcloud
BQ=~/google-cloud-sdk/bin/bq
PROJECT="dashboard-koti-omie"
DATASET="studio_koti"
SA_NAME="omie-sync"

echo "=================================================="
echo "🔧 SETUP GCP — Dashboard Koti"
echo "=================================================="

# 1. Verificar auth
echo ""
echo "1️⃣  Verificando autenticação..."
$GCLOUD auth list 2>&1
ACCOUNT=$($GCLOUD auth list --format="value(account)" --filter="status:ACTIVE" 2>/dev/null | head -1)
if [ -z "$ACCOUNT" ]; then
    echo "❌ Não autenticado! Rode: gcloud auth login"
    exit 1
fi
echo "✅ Logado como: $ACCOUNT"

# 2. Criar projeto (ignora se já existe)
echo ""
echo "2️⃣  Criando projeto $PROJECT..."
$GCLOUD projects create $PROJECT --name="Dashboard Omie" 2>/dev/null || echo "  (projeto já existe)"
$GCLOUD config set project $PROJECT
echo "✅ Projeto configurado: $PROJECT"

# 3. Habilitar BigQuery API
echo ""
echo "3️⃣  Habilitando BigQuery API..."
$GCLOUD services enable bigquery.googleapis.com
echo "✅ BigQuery API habilitada"

# 4. Criar Service Account
echo ""
echo "4️⃣  Criando Service Account..."
$GCLOUD iam service-accounts create $SA_NAME \
    --display-name="Omie Sync Pipeline" 2>/dev/null || echo "  (SA já existe)"

SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

$GCLOUD projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/bigquery.dataEditor" \
    --quiet 2>/dev/null

$GCLOUD projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/bigquery.jobUser" \
    --quiet 2>/dev/null

echo "✅ Service Account: $SA_EMAIL"

# 5. Gerar chave JSON
echo ""
echo "5️⃣  Gerando chave JSON..."
KEY_FILE="/tmp/gcp-key-koti.json"
$GCLOUD iam service-accounts keys create $KEY_FILE \
    --iam-account=$SA_EMAIL 2>/dev/null
echo "✅ Chave salva em: $KEY_FILE"

# 6. Criar dataset
echo ""
echo "6️⃣  Criando dataset $DATASET..."
$BQ mk --dataset --location=US "${PROJECT}:${DATASET}" 2>/dev/null || echo "  (dataset já existe)"
echo "✅ Dataset: ${PROJECT}:${DATASET}"

# 7. Executar schema SQL (criar tabelas)
echo ""
echo "7️⃣  Criando tabelas no BigQuery..."
SCHEMA_FILE=~/dashboard-koti/bq_schema.sql

# BigQuery não aceita arquivo SQL direto, executar statement por statement
# Separar por ; e executar cada um
while IFS= read -r -d ';' stmt; do
    # Limpar whitespace e pular vazios
    clean=$(echo "$stmt" | sed '/^$/d' | sed '/^--/d' | tr '\n' ' ' | xargs)
    if [ -n "$clean" ]; then
        echo "  Executando: $(echo "$clean" | head -c 60)..."
        $BQ query --use_legacy_sql=false --project_id=$PROJECT "$clean;" 2>&1 | tail -1
    fi
done < "$SCHEMA_FILE"
echo "✅ Tabelas criadas"

# 8. Mostrar resumo
echo ""
echo "=================================================="
echo "✅ SETUP COMPLETO!"
echo "=================================================="
echo ""
echo "Projeto:         $PROJECT"
echo "Dataset:         $DATASET"
echo "Service Account: $SA_EMAIL"
echo "Chave JSON:      $KEY_FILE"
echo ""
echo "📋 PRÓXIMOS PASSOS:"
echo ""
echo "1. Adicionar secrets no GitHub (repo dashboard-koti → Settings → Secrets):"
echo "   GCP_PROJECT_ID = $PROJECT"
echo "   GCP_SA_KEY     = $(cat $KEY_FILE | base64 | tr -d '\n' | head -c 50)... (copie o completo abaixo)"
echo ""
echo "Para copiar a chave base64 completa para o clipboard:"
echo "   cat $KEY_FILE | base64 | pbcopy"
echo ""
echo "2. Depois de adicionar os secrets, faça o push:"
echo "   cd ~/dashboard-koti && git add -A && git commit -m 'feat: BigQuery pipeline' && git push"
echo ""
echo "3. Rode o workflow manualmente:"
echo "   GitHub → Actions → 'Sync Omie → BigQuery' → Run workflow"
echo ""
echo "⚠️  IMPORTANTE: delete a chave local depois de salvar no GitHub:"
echo "   rm -f $KEY_FILE"
echo ""
