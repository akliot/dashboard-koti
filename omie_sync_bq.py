#!/usr/bin/env python3
"""
Omie API Data Sync → BigQuery
Coleta dados da API Omie e escreve no BigQuery para consumo pelo Looker Studio.

Adaptado de omie_sync.py (v6 incremental) — mantém toda a lógica de coleta,
substitui output JSON por BigQuery.

Variáveis de ambiente:
  OMIE_APP_KEY, OMIE_APP_SECRET  — credenciais Omie
  GCP_PROJECT_ID                 — projeto GCP
  BQ_DATASET                     — dataset BigQuery (default: studio_koti)
  GOOGLE_APPLICATION_CREDENTIALS — path para JSON da service account

Uso:
  pip install requests google-cloud-bigquery db-dtypes
  python omie_sync_bq.py
"""

import os
import sys
import re
import time
import uuid
import requests
from datetime import datetime, timedelta
from typing import Any, Optional

from google.cloud import bigquery

# ============================================================
# CONFIGURAÇÃO
# ============================================================
OMIE_APP_KEY = os.environ.get("OMIE_APP_KEY", "")
OMIE_APP_SECRET = os.environ.get("OMIE_APP_SECRET", "")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
BQ_DATASET = os.environ.get("BQ_DATASET", "studio_koti")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")

BASE_URL = "https://app.omie.com.br/api/v1"

# ⚡ KOTI-SPECIFIC: Contas correntes a IGNORAR (não representam movimentação real)
CONTAS_IGNORAR = {
    8754849088,  # BAIXA DE NFS - conta fictícia para baixa de notas
}

# Contas monitoradas para conciliação bancária diária (v1)
# BTG - Master excluído da v1; incluir somente com decisão explícita.
CONTAS_CONCILIACAO_V1: set[int] = {
    8621155275,  # BTG Pactual
}


# ============================================================
# BIGQUERY HELPERS
# ============================================================

def get_bq_client() -> bigquery.Client:
    """Inicializa o cliente BigQuery."""
    return bigquery.Client(project=GCP_PROJECT_ID)


def table_ref(table_name: str) -> str:
    """Retorna referência completa da tabela."""
    return f"{GCP_PROJECT_ID}.{BQ_DATASET}.{table_name}"


def ensure_tables(client: bigquery.Client) -> None:
    """Cria dataset e tabelas se não existirem (DDL)."""
    ds_ref = f"{GCP_PROJECT_ID}.{BQ_DATASET}"
    try:
        client.get_dataset(ds_ref)
    except Exception:
        print(f"  📦 Criando dataset {BQ_DATASET}...")
        client.create_dataset(ds_ref, exists_ok=True)

    ddl_statements = [
        f"""CREATE TABLE IF NOT EXISTS `{ds_ref}.sync_log` (
            sync_id STRING, started_at TIMESTAMP, finished_at TIMESTAMP,
            status STRING, duration_seconds INT64,
            lancamentos_count INT64, saldos_count INT64, clientes_count INT64,
            projetos_count INT64, categorias_count INT64,
            error_message STRING, is_incremental BOOL
        )""",
        f"""CREATE TABLE IF NOT EXISTS `{ds_ref}.lancamentos` (
            id INT64, tipo STRING, valor FLOAT64, status STRING,
            data_vencimento DATE, data_emissao DATE, data_pagamento DATE, data_previsao DATE,
            numero_documento STRING,
            categoria_codigo STRING, categoria_nome STRING, categoria_grupo STRING,
            projeto_id INT64, projeto_nome STRING, cliente_id INT64, cliente_nome STRING,
            conta_corrente_id INT64, is_faturamento_direto BOOL, modalidade STRING,
            sync_timestamp TIMESTAMP, sync_date DATE
        )""",
        f"""CREATE TABLE IF NOT EXISTS `{ds_ref}.saldos_bancarios` (
            conta_id INT64, conta_nome STRING, conta_tipo STRING,
            saldo FLOAT64, saldo_conciliado FLOAT64, diferenca FLOAT64,
            data_referencia DATE, sync_timestamp TIMESTAMP, sync_date DATE
        )""",
        f"""CREATE TABLE IF NOT EXISTS `{ds_ref}.historico_saldos` (
            conta_id INT64, conta_nome STRING, data_referencia DATE, label STRING,
            saldo_atual FLOAT64, saldo_conciliado FLOAT64, diferenca FLOAT64,
            tipo STRING, sync_timestamp TIMESTAMP, sync_date DATE
        )""",
        f"""CREATE TABLE IF NOT EXISTS `{ds_ref}.categorias` (
            codigo STRING, nome STRING, grupo STRING, sync_timestamp TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS `{ds_ref}.projetos` (
            id INT64, nome STRING, sync_timestamp TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS `{ds_ref}.clientes` (
            id INT64, nome_fantasia STRING, razao_social STRING, estado STRING,
            ativo BOOL, pessoa_fisica BOOL, data_cadastro DATE, sync_timestamp TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS `{ds_ref}.vendas_pedidos` (
            pedido_id INT64, valor_mercadorias FLOAT64, etapa STRING,
            data_previsao DATE, produto_descricao STRING, produto_quantidade FLOAT64,
            produto_valor_total FLOAT64, sync_timestamp TIMESTAMP, sync_date DATE
        )""",
        f"""CREATE TABLE IF NOT EXISTS `{ds_ref}.orcamento_dre` (
            label STRING, section STRING, level INT64, mes STRING,
            valor_real FLOAT64, valor_bp FLOAT64, variacao_pct FLOAT64,
            mes_com_real BOOL, sync_timestamp TIMESTAMP
        )""",
        f"""CREATE OR REPLACE VIEW `{ds_ref}.v_historico_saldos` AS
        SELECT * EXCEPT(rn) FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY conta_id, data_referencia, tipo
                ORDER BY sync_timestamp DESC
            ) AS rn
            FROM `{ds_ref}.historico_saldos`
        ) WHERE rn = 1""",
        f"""CREATE TABLE IF NOT EXISTS `{ds_ref}.conciliacao_bancaria_diaria` (
            data_referencia DATE,
            banco STRING,
            conta_id INT64,
            conta_nome STRING,
            saldo_final FLOAT64,
            saldo_conciliado FLOAT64,
            saldo_ultimo_movimento_dia FLOAT64,
            gap_conciliacao FLOAT64,
            qtd_movimentos INT64,
            sync_timestamp TIMESTAMP,
            sync_date DATE
        )
        PARTITION BY data_referencia
        CLUSTER BY banco, conta_id
        OPTIONS(partition_expiration_days = NULL)""",
    ]

    created = 0
    for ddl in ddl_statements:
        try:
            client.query(ddl).result()
            created += 1
        except Exception as e:
            print(f"  ⚠ DDL falhou: {e}")
    print(f"  ✅ {created}/{len(ddl_statements)} tabelas verificadas/criadas")


def load_to_bq(
    client: bigquery.Client,
    table_name: str,
    rows: list[dict],
    write_disposition: str = "WRITE_TRUNCATE",
) -> int:
    """Carrega lista de dicts no BigQuery (full replace). Retorna número de linhas."""
    if not rows:
        print(f"  ⚠ {table_name}: 0 registros — pulando")
        return 0

    ref = table_ref(table_name)
    try:
        target_schema = client.get_table(ref).schema
    except Exception:
        target_schema = None
    job_config = bigquery.LoadJobConfig(
        write_disposition=write_disposition,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=target_schema if target_schema else None,
        autodetect=target_schema is None,
    )

    job = client.load_table_from_json(rows, ref, job_config=job_config)
    job.result()
    print(f"  ✅ {table_name}: {len(rows)} registros carregados ({write_disposition})")
    return len(rows)


def merge_to_bq(
    client: bigquery.Client,
    table_name: str,
    rows: list[dict],
    key_column: str | list[str],
    compare_columns: list[str],
    all_columns: list[str],
    delete_filter: str | None = None,
    no_delete: bool = False,
) -> dict[str, int]:
    """Sync incremental via MERGE: compara por key, atualiza mudanças, insere novos, remove deletados.
    key_column pode ser str (simples) ou list[str] (chave composta).
    no_delete=True: omite DELETE completamente (tabelas de histórico preservado).
    delete_filter: limita DELETE a uma janela de datas (ignorado se no_delete=True).
    Retorna {"inserted": N, "updated": N, "deleted": N, "unchanged": N}."""
    if not rows:
        print(f"  ⚠ {table_name}: 0 registros da API — pulando")
        return {"inserted": 0, "updated": 0, "deleted": 0, "unchanged": 0}

    keys = [key_column] if isinstance(key_column, str) else list(key_column)
    keys_set = set(keys)

    staging = f"{table_ref(table_name)}_staging"
    target = table_ref(table_name)

    # 1. Carregar na staging (WRITE_TRUNCATE)
    # Usa schema da tabela destino para evitar inferência errada de colunas all-null.
    try:
        target_schema = client.get_table(target).schema
    except Exception:
        target_schema = None
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=target_schema if target_schema else None,
        autodetect=target_schema is None,
    )
    job = client.load_table_from_json(rows, staging, job_config=job_config)
    job.result()

    # 2. Contar estado atual para estatísticas
    try:
        current_count = list(client.query(f"SELECT COUNT(*) as c FROM `{target}`").result())[0].c
    except Exception:
        current_count = 0

    # 3. MERGE: insert novos, update mudados, delete removidos
    on_clause = " AND ".join(f"T.{k} = S.{k}" for k in keys)
    update_set = ", ".join(f"T.{c} = S.{c}" for c in all_columns if c not in keys_set)
    compare_or = " OR ".join(
        f"IFNULL(CAST(T.{c} AS STRING),'') != IFNULL(CAST(S.{c} AS STRING),'')"
        for c in compare_columns
    )
    insert_cols = ", ".join(all_columns)
    insert_vals = ", ".join(f"S.{c}" for c in all_columns)
    if no_delete:
        delete_clause = ""
    elif delete_filter:
        delete_clause = f"WHEN NOT MATCHED BY SOURCE AND {delete_filter} THEN DELETE"
    else:
        delete_clause = "WHEN NOT MATCHED BY SOURCE THEN DELETE"

    merge_sql = f"""
    MERGE `{target}` AS T
    USING `{staging}` AS S
    ON {on_clause}
    WHEN MATCHED AND ({compare_or})
        THEN UPDATE SET {update_set}
    WHEN NOT MATCHED BY TARGET
        THEN INSERT ({insert_cols}) VALUES ({insert_vals})
    {delete_clause}
    """

    job = client.query(merge_sql)
    result = job.result()
    stats = job.num_dml_affected_rows or 0

    # 4. Contar resultado
    new_count = list(client.query(f"SELECT COUNT(*) as c FROM `{target}`").result())[0].c
    staging_count = len(rows)

    inserted = max(0, new_count - current_count)
    deleted = max(0, current_count - (new_count - inserted) if inserted > 0 else current_count - new_count)
    # DML affected = inserted + updated + deleted
    updated = max(0, stats - inserted - deleted)
    unchanged = staging_count - inserted - updated

    # 5. Limpar staging
    try:
        client.delete_table(staging, not_found_ok=True)
    except Exception:
        pass

    print(f"  ✅ {table_name}: {inserted} novos, {updated} atualizados, {deleted} removidos, {unchanged} iguais")
    return {"inserted": inserted, "updated": updated, "deleted": deleted, "unchanged": unchanged}


# ============================================================
# FUNÇÕES AUXILIARES — API OMIE
# ============================================================

def omie_request(endpoint: str, call: str, params: dict, retries: int = 3) -> Optional[dict]:
    """Faz uma requisição à API Omie com retry."""
    url = f"{BASE_URL}/{endpoint}/"
    payload = {
        "call": call,
        "app_key": OMIE_APP_KEY,
        "app_secret": OMIE_APP_SECRET,
        "param": [params],
    }
    for attempt in range(retries):
        try:
            resp = requests.post(url, json=payload, timeout=60)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                print(f"  ⚠ Erro: {call} - {e}")
                return None


def paginar(endpoint: str, call: str, param_base: dict, lista_key: str, max_pages: int = 200) -> list:
    """Pagina resultados da API Omie."""
    todos = []
    pagina = 1
    total_paginas = 1
    while pagina <= min(total_paginas, max_pages):
        params = {**param_base, "pagina": pagina, "registros_por_pagina": 200}
        data = omie_request(endpoint, call, params)
        if not data:
            break
        total_paginas = data.get("total_de_paginas", 1)
        registros = data.get(lista_key, [])
        todos.extend(registros)
        if pagina % 10 == 0 or pagina == total_paginas:
            print(f"  {call} pag {pagina}/{total_paginas}", flush=True)
        pagina += 1
    return todos


def parse_date(date_str: str) -> Optional[str]:
    """Converte DD/MM/YYYY → YYYY-MM-DD. Retorna None se inválido."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%d/%m/%Y")
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _limpar_nome_categoria(desc: str) -> str:
    """Remove prefixo numérico tipo '2.01.03 - ' do nome da categoria."""
    return re.sub(r'^\d+(\.\d+)+ - ', '', desc).strip()


def _derivar_banco(conta_nome: str) -> str:
    nome = conta_nome.lower()
    if "btg" in nome:
        return "BTG"
    if "xp" in nome:
        return "XP"
    if "itau" in nome or "itaú" in nome:
        return "Itau"
    return "Outro"


# ============================================================
# COLETA DE DADOS
# ============================================================

def coletar_categorias() -> dict[str, str]:
    """Coleta categorias do Omie. Retorna {codigo: nome}."""
    print("\n📥 Categorias...", flush=True)
    data = omie_request("geral/categorias", "ListarCategorias", {"pagina": 1, "registros_por_pagina": 500})
    cat_map: dict[str, str] = {}
    if data:
        for c in data.get("categoria_cadastro", []):
            cod = c.get("codigo", "")
            desc = c.get("descricao", "")
            if "<Disponível>" not in desc and desc:
                cat_map[cod] = _limpar_nome_categoria(desc)
    print(f"  ✅ {len(cat_map)} categorias")
    return cat_map


def completar_categorias(cat_map: dict[str, str], lancamentos_raw: list[dict]) -> dict[str, str]:
    """Busca categorias faltantes via ConsultarCategoria."""
    codigos_usados: set[str] = set()
    for l in lancamentos_raw:
        cat = l.get("codigo_categoria") or ""
        if cat:
            codigos_usados.add(cat)
            codigos_usados.add(".".join(cat.split(".")[:2]))

    faltantes = [c for c in sorted(codigos_usados) if c and c not in cat_map]
    if not faltantes:
        print(f"  ✅ Todas categorias já conhecidas")
        return cat_map

    print(f"  🔍 Buscando {len(faltantes)} categorias faltantes...")
    encontrados = 0
    for cod in faltantes:
        try:
            data = omie_request("geral/categorias", "ConsultarCategoria", {"codigo": cod})
            if data and data.get("descricao"):
                desc = data["descricao"]
                if "<Disponível>" not in desc:
                    cat_map[cod] = _limpar_nome_categoria(desc)
                    encontrados += 1
            time.sleep(0.05)
        except Exception:
            pass
    print(f"  ✅ {encontrados} categorias adicionais (total: {len(cat_map)})")
    return cat_map


def coletar_projetos() -> dict[int, str]:
    """Coleta projetos do Omie. Retorna {id: nome}."""
    print("\n📥 Projetos...", flush=True)
    registros = paginar("geral/projetos", "ListarProjetos", {}, "cadastro")
    proj_map: dict[int, str] = {}
    for p in registros:
        pid = p.get("codigo")
        nome = p.get("nome", "")
        if pid and nome:
            proj_map[pid] = nome
    print(f"  ✅ {len(proj_map)} projetos")
    return proj_map


def _extrato_snapshot(cc_id: int, d_ini: str, d_fim: str) -> Optional[dict]:
    """Busca saldos de uma conta em um período."""
    ext = omie_request("financas/extrato", "ListarExtrato", {
        "nCodCC": cc_id,
        "dPeriodoInicial": d_ini,
        "dPeriodoFinal": d_fim,
    })
    if not ext:
        return None
    return {
        "saldo": float(ext.get("nSaldoAtual", 0) or 0),
        "saldo_conciliado": float(ext.get("nSaldoConciliado", 0) or 0),
    }


def coletar_saldos_bancarios(
    client: bigquery.Client, sync_ts: str, sync_date: str
) -> tuple[list[dict], list[dict]]:
    """Coleta saldos bancários (snapshot D-1) e histórico mensal+diário.
    Usa BigQuery como cache: meses passados já existentes não são re-buscados na API."""
    print("\n📥 Saldos Bancários + Conciliação...", flush=True)
    data = omie_request("geral/contacorrente", "ListarContasCorrentes",
                        {"pagina": 1, "registros_por_pagina": 200})
    if not data:
        print("  ⚠ Sem dados de contas correntes")
        return [], []

    contas_raw = data.get("ListarContasCorrentes", [])
    hoje = datetime.now()
    ontem = hoje - timedelta(days=1)
    d_fim = ontem.strftime("%d/%m/%Y")

    # ---- 1) Saldos snapshot D-1 ----
    saldos: list[dict] = []
    contas_ativas: list[tuple[int, str]] = []
    for cc in contas_raw:
        cc_id = cc.get("nCodCC")
        if not cc_id or cc_id in CONTAS_IGNORAR:  # ⚡ KOTI-SPECIFIC
            continue
        nome = cc.get("descricao", "Sem nome")
        tipo = cc.get("cCodTipo", cc.get("tipo", ""))

        snap = _extrato_snapshot(cc_id, f"01/01/{ontem.year}", d_fim)
        saldo = snap["saldo"] if snap else 0
        saldo_conc = snap["saldo_conciliado"] if snap else 0
        dif = round(saldo - saldo_conc, 2)

        saldos.append({
            "conta_id": cc_id,
            "conta_nome": nome,
            "conta_tipo": tipo,
            "saldo": saldo,
            "saldo_conciliado": saldo_conc,
            "diferenca": dif,
            "data_referencia": ontem.strftime("%Y-%m-%d"),
            "sync_timestamp": sync_ts,
            "sync_date": sync_date,
        })
        if saldo != 0 or saldo_conc != 0:
            contas_ativas.append((cc_id, nome))
            print(f"  {nome}: Saldo={saldo:.2f} Concil={saldo_conc:.2f} Dif={dif:.2f}", flush=True)

    print(f"  ✅ {len(saldos)} contas ({len(contas_ativas)} ativas)", flush=True)

    # ---- 2) Histórico mensal + diário ----
    print("\n📥 Histórico de Conciliação...", flush=True)
    historico: list[dict] = []

    meses_fim = []
    dt_cursor = hoje.replace(day=1) - timedelta(days=1)
    for _ in range(6):
        meses_fim.insert(0, dt_cursor)
        dt_cursor = dt_cursor.replace(day=1) - timedelta(days=1)

    dias_corrente = []
    for day in range(1, hoje.day + 1, 5):
        dias_corrente.append(datetime(hoje.year, hoje.month, day))
    if dias_corrente and dias_corrente[-1].day != hoje.day:
        dias_corrente.append(datetime(hoje.year, hoje.month, hoje.day))

    print(f"  {len(contas_ativas)} contas × ({len(meses_fim)} meses + {len(dias_corrente)} dias)", flush=True)

    # ---- Cache: buscar meses já existentes no BigQuery ----
    mes_corrente = hoje.strftime("%Y-%m")
    cached_mensal: set[tuple[int, str]] = set()  # (conta_id, data_referencia)
    try:
        query = f"""
            SELECT DISTINCT conta_id, CAST(data_referencia AS STRING) as data_ref
            FROM `{table_ref('historico_saldos')}`
            WHERE tipo = 'mensal'
        """
        for row in client.query(query):
            # Não cachear mês corrente (dados ainda mudam)
            if not row.data_ref.startswith(mes_corrente):
                cached_mensal.add((row.conta_id, row.data_ref))
        print(f"  📦 {len(cached_mensal)} registros mensais já no BigQuery (cache)", flush=True)
    except Exception as e:
        print(f"  ⚠ Cache BQ indisponível ({e}) — buscando tudo da API", flush=True)

    calls_cached = 0
    calls_api = 0

    for cc_id, nome in contas_ativas:
        for dt in meses_fim:
            dt_str = dt.strftime("%Y-%m-%d")
            if (cc_id, dt_str) in cached_mensal:
                calls_cached += 1
                continue  # Já existe no BQ — pular
            calls_api += 1
            time.sleep(0.05)
            snap = _extrato_snapshot(cc_id, f"01/{dt.month:02d}/{dt.year}", dt.strftime("%d/%m/%Y"))
            if snap:
                historico.append({
                    "conta_id": cc_id,
                    "conta_nome": nome,
                    "data_referencia": dt_str,
                    "label": dt.strftime("%b/%y"),
                    "saldo_atual": snap["saldo"],
                    "saldo_conciliado": snap["saldo_conciliado"],
                    "diferenca": round(snap["saldo"] - snap["saldo_conciliado"], 2),
                    "tipo": "mensal",
                    "sync_timestamp": sync_ts,
                    "sync_date": sync_date,
                })

        for dt in dias_corrente:
            time.sleep(0.05)
            snap = _extrato_snapshot(cc_id, f"01/{dt.month:02d}/{dt.year}", dt.strftime("%d/%m/%Y"))
            if snap:
                historico.append({
                    "conta_id": cc_id,
                    "conta_nome": nome,
                    "data_referencia": dt.strftime("%Y-%m-%d"),
                    "label": dt.strftime("%d/%m"),
                    "saldo_atual": snap["saldo"],
                    "saldo_conciliado": snap["saldo_conciliado"],
                    "diferenca": round(snap["saldo"] - snap["saldo_conciliado"], 2),
                    "tipo": "diario",
                    "sync_timestamp": sync_ts,
                    "sync_date": sync_date,
                })

        print(f"  ✅ {nome}", flush=True)

    print(f"  ✅ {len(historico)} registros novos de histórico ({calls_cached} do cache BQ, {calls_api} da API)", flush=True)
    return saldos, historico


def coletar_conciliacao_bancaria(
    sync_ts: str, sync_date: str, data_ref: Optional[str] = None
) -> list[dict]:
    """Coleta conciliação bancária diária para contas monitoradas (v1: BTG Pactual).

    Retorna lista de dicts pronta para load_to_bq — não escreve no BigQuery.
    BTG - Master excluído da v1; incluir somente com decisão explícita.
    data_ref: YYYY-MM-DD a coletar (default: D-1).
    """
    if data_ref is None:
        ontem = datetime.now() - timedelta(days=1)
        data_ref = ontem.strftime("%Y-%m-%d")
        dia_omie = ontem.strftime("%d/%m/%Y")
    else:
        dt_ref = datetime.strptime(data_ref, "%Y-%m-%d")
        dia_omie = dt_ref.strftime("%d/%m/%Y")

    print(f"\n📥 Conciliação Bancária Diária ({data_ref})...", flush=True)

    data = omie_request("geral/contacorrente", "ListarContasCorrentes",
                        {"pagina": 1, "registros_por_pagina": 200})
    if not data:
        print("  ⚠ Sem dados de contas correntes")
        return []

    linhas: list[dict] = []
    for cc in data.get("ListarContasCorrentes", []):
        cc_id = cc.get("nCodCC")
        nome = cc.get("descricao", "") or ""

        if cc_id not in CONTAS_CONCILIACAO_V1:
            continue
        if "master" in nome.lower():
            print(f"  ⏭ Excluindo '{nome}' (Master — fora do escopo v1)")
            continue

        time.sleep(0.2)
        ext = omie_request("financas/extrato", "ListarExtrato", {
            "nCodCC": cc_id,
            "dPeriodoInicial": dia_omie,
            "dPeriodoFinal": dia_omie,
        })
        if not ext:
            print(f"  ⚠ Sem resposta para {nome} ({data_ref})")
            continue

        saldo_final = float(ext.get("nSaldoAtual", 0) or 0)
        saldo_conc = float(ext.get("nSaldoConciliado", 0) or 0)
        movimentos = ext.get("listaMovimentos") or []

        ult_saldo: Optional[float] = None
        for m in reversed(movimentos):
            v = m.get("nSaldo")
            if v is not None:
                try:
                    ult_saldo = float(v)
                    break
                except (ValueError, TypeError):
                    pass

        gap = round(saldo_final - saldo_conc, 2)
        linhas.append({
            "data_referencia": data_ref,
            "banco": _derivar_banco(nome),
            "conta_id": cc_id,
            "conta_nome": nome,
            "saldo_final": round(saldo_final, 2),
            "saldo_conciliado": round(saldo_conc, 2),
            "saldo_ultimo_movimento_dia": round(ult_saldo, 2) if ult_saldo is not None else None,
            "gap_conciliacao": gap,
            "qtd_movimentos": len(movimentos),
            "sync_timestamp": sync_ts,
            "sync_date": sync_date,
        })
        print(f"  ✅ {nome}: saldo={saldo_final:.2f} conc={saldo_conc:.2f} gap={gap:+.2f} movs={len(movimentos)}")

    print(f"  ✅ {len(linhas)} linha(s) conciliação bancária ({data_ref})")
    return linhas


def carregar_conciliacao_bancaria(
    client: bigquery.Client,
    rows: list[dict],
    data_ref: str,
) -> int:
    """Grava rows em conciliacao_bancaria_diaria por chave (data_referencia, conta_id).

    Idempotente: apaga somente as contas presentes no lote para data_ref e reinsere.
    Isso permite recarregar apenas BTG em um dia histórico sem apagar XP/Itau do mesmo dia.

    NÃO está plugada no main() — aprovação explícita necessária antes de usar.
    data_ref: YYYY-MM-DD.
    """
    if not rows:
        print("  ⚠ carregar_conciliacao_bancaria: 0 registros — pulando")
        return 0

    target = table_ref("conciliacao_bancaria_diaria")
    staging = f"{target}_staging_{uuid.uuid4().hex}"
    data_ref_date = datetime.strptime(data_ref, "%Y-%m-%d").date()

    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        autodetect=True,
    )
    job = client.load_table_from_json(rows, staging, job_config=job_config)
    job.result()

    cols = [
        "data_referencia", "banco", "conta_id", "conta_nome",
        "saldo_final", "saldo_conciliado", "saldo_ultimo_movimento_dia",
        "gap_conciliacao", "qtd_movimentos", "sync_timestamp", "sync_date",
    ]
    col_list = ", ".join(cols)
    select_list = ", ".join([
        "DATE(data_referencia) AS data_referencia",
        "banco",
        "CAST(conta_id AS INT64) AS conta_id",
        "conta_nome",
        "CAST(saldo_final AS FLOAT64) AS saldo_final",
        "CAST(saldo_conciliado AS FLOAT64) AS saldo_conciliado",
        "CAST(saldo_ultimo_movimento_dia AS FLOAT64) AS saldo_ultimo_movimento_dia",
        "CAST(gap_conciliacao AS FLOAT64) AS gap_conciliacao",
        "CAST(qtd_movimentos AS INT64) AS qtd_movimentos",
        "TIMESTAMP(sync_timestamp) AS sync_timestamp",
        "DATE(sync_date) AS sync_date",
    ])

    job_params = [
        bigquery.ScalarQueryParameter("data_ref", "DATE", data_ref_date),
    ]

    try:
        delete_sql = f"""
        DELETE FROM `{target}`
        WHERE data_referencia = @data_ref
          AND conta_id IN (
              SELECT DISTINCT CAST(conta_id AS INT64)
              FROM `{staging}`
              WHERE DATE(data_referencia) = @data_ref
          )
        """
        client.query(
            delete_sql,
            job_config=bigquery.QueryJobConfig(query_parameters=job_params),
        ).result()

        insert_sql = f"""
        INSERT INTO `{target}` ({col_list})
        SELECT {select_list}
        FROM `{staging}`
        WHERE DATE(data_referencia) = @data_ref
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY DATE(data_referencia), CAST(conta_id AS INT64)
            ORDER BY sync_timestamp DESC
        ) = 1
        """
        result = client.query(
            insert_sql,
            job_config=bigquery.QueryJobConfig(query_parameters=job_params),
        ).result()
        inserted = result.num_dml_affected_rows or len(rows)
        print(f"  ✅ conciliacao_bancaria_diaria: {inserted} linha(s) gravadas para {data_ref}")
        return inserted
    finally:
        client.delete_table(staging, not_found_ok=True)


def gerar_datas_periodo(data_ini: str, data_fim: str) -> list[str]:
    """Retorna lista de datas YYYY-MM-DD entre data_ini e data_fim (inclusive)."""
    fmt = "%Y-%m-%d"
    atual = datetime.strptime(data_ini, fmt)
    fim = datetime.strptime(data_fim, fmt)
    datas: list[str] = []
    while atual <= fim:
        datas.append(atual.strftime(fmt))
        atual += timedelta(days=1)
    return datas


def coletar_conciliacao_bancaria_periodo(
    data_ini: str, data_fim: str, sync_ts: str, sync_date: str
) -> list[dict]:
    """Coleta conciliação bancária para um período completo de datas.

    Para cada dia em [data_ini, data_fim], chama coletar_conciliacao_bancaria().
    Retorna lista plana de todos os registros — não escreve no BigQuery.

    Uso: revalidação retroativa (semanal, fechamento mensal, auditoria histórica).
    Contexto: Omie e o banco podem ajustar saldos/conciliações retroativamente;
    um lançamento apagado hoje pode alterar nSaldoConciliado de datas passadas.

    NÃO está plugada no main() — aprovação explícita necessária antes de usar.
    data_ini, data_fim: YYYY-MM-DD.
    """
    datas = gerar_datas_periodo(data_ini, data_fim)
    print(f"\n📅 Coleta de período: {data_ini} → {data_fim} ({len(datas)} dias)", flush=True)
    todas: list[dict] = []
    for data_ref in datas:
        rows = coletar_conciliacao_bancaria(sync_ts, sync_date, data_ref=data_ref)
        todas.extend(rows)
    print(f"  ✅ {len(todas)} linha(s) coletadas no período")
    return todas


def construir_mapa_clientes_bulk() -> tuple[dict[int, str], list[dict]]:
    """Busca TODOS os clientes via ListarClientes (bulk).
    Retorna (cli_map, registros_raw)."""
    print("  🔍 Buscando todos clientes via ListarClientes (bulk)...")
    registros = paginar("geral/clientes", "ListarClientes",
                        {"clientesFiltro": {"codigo_cliente_omie": 0}}, "clientes_cadastro")
    cli_map: dict[int, str] = {}
    for r in registros:
        cid = r.get("codigo_cliente_omie")
        nome = r.get("nome_fantasia") or r.get("razao_social") or ""
        if cid and nome:
            cli_map[cid] = nome
    print(f"  ✅ {len(cli_map)} clientes mapeados (bulk)")
    return cli_map, registros


def coletar_fd_do_extrato() -> set[int]:
    """Coleta IDs de lançamentos que são Faturamento Direto via extrato bancário.
    O extrato tem cDocumentoFiscal com 'FD' que não aparece no ListarContasPagar.
    Retorna set de nCodLancRelac (IDs do extrato, não do CP — match por MF depois)."""
    # Não implementável por match direto (IDs incompatíveis).
    # Usamos abordagem alternativa no _extract: checar NF no ConsultarContaPagar.
    return set()


def coletar_movimentos_financeiros() -> dict[int, str]:
    """Coleta datas reais de pagamento/recebimento via Movimentos Financeiros.
    Retorna {nCodTitulo: data_pagamento_real (YYYY-MM-DD)}.
    nCodTitulo = codigo_lancamento_omie do CP/CR → link direto."""
    print("\n📥 Movimentos Financeiros (datas reais)...", flush=True)
    mf_map: dict[int, str] = {}
    pagina = 1
    total_paginas = 1

    while pagina <= total_paginas:
        data = omie_request("financas/mf", "ListarMovimentos",
                            {"nPagina": pagina, "nRegPorPagina": 500})
        if not data:
            break
        total_paginas = data.get("nTotPaginas", 1)
        movimentos = data.get("movimentos", [])

        for m in movimentos:
            det = m.get("detalhes", {})
            cod = det.get("nCodTitulo")
            dt_pag = det.get("dDtPagamento", "")
            if cod and dt_pag:
                parsed = parse_date(dt_pag)
                if parsed:
                    mf_map[cod] = parsed  # Se duplicado, último ganha

        if pagina % 10 == 0 or pagina == total_paginas:
            print(f"  ListarMovimentos pag {pagina}/{total_paginas} ({len(mf_map)} datas)", flush=True)
        pagina += 1
        time.sleep(0.1)

    print(f"  ✅ {len(mf_map)} datas reais de pagamento via MF")
    return mf_map



def coletar_lancamentos(
    cat_map: dict[str, str],
    proj_map: dict[int, str],
    cli_map: dict[int, str],
    sync_ts: str,
    sync_date: str,
    mf_datas: Optional[dict[int, str]] = None,
) -> list[dict]:
    """Coleta lançamentos (contas a receber + pagar) e transforma para schema BQ."""
    lancamentos: list[dict] = []

    # Contas a Receber
    print("\n📥 Contas a Receber...", flush=True)
    cr_raw = paginar("financas/contareceber", "ListarContasReceber", {}, "conta_receber_cadastro")

    # Contas a Pagar
    print("\n📥 Contas a Pagar...", flush=True)
    cp_raw = paginar("financas/contapagar", "ListarContasPagar", {}, "conta_pagar_cadastro")

    # Completar categorias faltantes
    cat_map = completar_categorias(cat_map, cr_raw + cp_raw)

    mf = mf_datas or {}
    match_mf = 0
    match_fallback = 0

    def _extract_data_pagamento(r: dict) -> Optional[str]:
        """Extrai data real de pagamento/recebimento.
        Prioridade: Movimentos Financeiros (dDtPagamento via nCodTitulo) > data_previsao."""
        nonlocal match_mf, match_fallback
        status = (r.get("status_titulo", "") or "").upper()
        if status not in ("PAGO", "RECEBIDO", "LIQUIDADO"):
            return None
        # 1. Movimentos Financeiros (link direto por nCodTitulo)
        cod = r.get("codigo_lancamento_omie")
        if cod and cod in mf:
            match_mf += 1
            return mf[cod]
        # 2. Fallback: data_previsao
        d_prev = parse_date(r.get("data_previsao", ""))
        if d_prev:
            match_fallback += 1
            return d_prev
        return None

    ignorados_cr = 0
    for r in cr_raw:
        if r.get("id_conta_corrente") in CONTAS_IGNORAR:  # ⚡ KOTI-SPECIFIC
            ignorados_cr += 1
            continue
        cat_cod = r.get("codigo_categoria", "")
        cat_nome = cat_map.get(cat_cod, cat_cod)
        proj_id = r.get("codigo_projeto")
        cli_id = r.get("codigo_cliente_fornecedor")
        num_doc = r.get("numero_documento", "") or ""

        # ⚡ KOTI-SPECIFIC: Faturamento Direto — entrada: categoria contém "Faturamento Direto"
        is_fd = "faturamento direto" in (cat_nome or "").lower()

        cat_grupo = ".".join(cat_cod.split(".")[:2]) if cat_cod else None

        lancamentos.append({
            "id": r.get("codigo_lancamento_omie"),
            "tipo": "entrada",
            "valor": float(r.get("valor_documento", 0) or 0),
            "status": (r.get("status_titulo", "") or "").upper(),
            "data_vencimento": parse_date(r.get("data_vencimento", "")),
            "data_emissao": parse_date(r.get("data_emissao", "")),
            "data_pagamento": _extract_data_pagamento(r),
            "data_previsao": parse_date(r.get("data_previsao", "")) or parse_date(r.get("data_vencimento", "")),
            "numero_documento": num_doc,
            "categoria_codigo": cat_cod or None,
            "categoria_nome": cat_nome or None,
            "categoria_grupo": cat_grupo,
            "projeto_id": proj_id,
            "projeto_nome": proj_map.get(proj_id, "Sem projeto") if proj_id else "Sem projeto",
            "cliente_id": cli_id,
            "cliente_nome": cli_map.get(cli_id, ""),
            "conta_corrente_id": r.get("id_conta_corrente"),
            "is_faturamento_direto": is_fd,
            "modalidade": "FD" if is_fd else "SK",
            "sync_timestamp": sync_ts,
            "sync_date": sync_date,
        })
    print(f"  ✅ {len(cr_raw) - ignorados_cr} receber ({ignorados_cr} ignorados)")

    ignorados_cp = 0
    for r in cp_raw:
        if r.get("id_conta_corrente") in CONTAS_IGNORAR:  # ⚡ KOTI-SPECIFIC
            ignorados_cp += 1
            continue
        cat_cod = r.get("codigo_categoria", "")
        cat_nome = cat_map.get(cat_cod, cat_cod)
        proj_id = r.get("codigo_projeto")
        cli_id = r.get("codigo_cliente_fornecedor")
        num_doc = r.get("numero_documento", "") or ""

        # ⚡ KOTI-SPECIFIC: Faturamento Direto — saída: NF ou documento contém "FD"
        num_nf = r.get("numero_documento_fiscal", "") or ""
        is_fd = "fd" in num_doc.lower() or "fd" in num_nf.lower()

        cat_grupo = ".".join(cat_cod.split(".")[:2]) if cat_cod else None

        lancamentos.append({
            "id": r.get("codigo_lancamento_omie"),
            "tipo": "saida",
            "valor": float(r.get("valor_documento", 0) or 0),
            "status": (r.get("status_titulo", "") or "").upper(),
            "data_vencimento": parse_date(r.get("data_vencimento", "")),
            "data_emissao": parse_date(r.get("data_emissao", "")),
            "data_pagamento": _extract_data_pagamento(r),
            "data_previsao": parse_date(r.get("data_previsao", "")) or parse_date(r.get("data_vencimento", "")),
            "numero_documento": num_doc,
            "categoria_codigo": cat_cod or None,
            "categoria_nome": cat_nome or None,
            "categoria_grupo": cat_grupo,
            "projeto_id": proj_id,
            "projeto_nome": proj_map.get(proj_id, "Sem projeto") if proj_id else "Sem projeto",
            "cliente_id": cli_id,
            "cliente_nome": cli_map.get(cli_id, ""),
            "conta_corrente_id": r.get("id_conta_corrente"),
            "is_faturamento_direto": is_fd,
            "modalidade": "FD" if is_fd else "SK",
            "sync_timestamp": sync_ts,
            "sync_date": sync_date,
        })
    fd_saida = sum(1 for l in lancamentos if l["tipo"] == "saida" and l["modalidade"] == "FD")
    fd_entrada = sum(1 for l in lancamentos if l["tipo"] == "entrada" and l["modalidade"] == "FD")
    print(f"  ✅ {len(cp_raw) - ignorados_cp} pagar ({ignorados_cp} ignorados)")
    print(f"  📊 Modalidade FD: {fd_entrada} entradas, {fd_saida} saídas")
    print(f"  📊 data_pagamento: {match_mf} via MF (real), {match_fallback} fallback (previsão)")

    return lancamentos


def coletar_clientes_bq(registros_raw: list[dict], sync_ts: str) -> list[dict]:
    """Transforma registros raw de clientes para schema BQ — 1 linha por cliente."""
    print("\n📥 Clientes (BQ)...")
    clientes: list[dict] = []
    for r in registros_raw:
        cid = r.get("codigo_cliente_omie")
        if not cid:
            continue
        dt_inc = r.get("info", {}).get("dInc", "")
        data_cadastro = parse_date(dt_inc[:10]) if dt_inc else None

        clientes.append({
            "id": cid,
            "nome_fantasia": r.get("nome_fantasia", ""),
            "razao_social": r.get("razao_social", ""),
            "estado": r.get("estado", "") or None,
            "ativo": r.get("inativo", "N") != "S",
            "pessoa_fisica": r.get("pessoa_fisica", "N") == "S",
            "data_cadastro": data_cadastro,
            "sync_timestamp": sync_ts,
        })
    print(f"  ✅ {len(clientes)} clientes")
    return clientes


def coletar_vendas_bq(sync_ts: str, sync_date: str) -> list[dict]:
    """Coleta pedidos de venda — 1 linha por item (explode)."""
    print("\n📥 Pedidos de Venda...")
    registros = paginar("produtos/pedido", "ListarPedidos",
                        {"apenas_importado_api": "N"}, "pedido_venda_produto")

    etapa_map = {
        "10": "Em Aberto", "20": "Em Aprovação", "30": "Aprovado",
        "40": "Separar", "50": "Em Separação", "60": "Faturado",
        "70": "Cancelado", "80": "Entregue",
    }

    vendas: list[dict] = []
    for r in registros:
        cab = r.get("cabecalho", {})
        tp = r.get("total_pedido", {})
        valor_merc = float(tp.get("valor_mercadorias", 0) or 0)
        etapa_cod = str(cab.get("etapa", ""))
        etapa = etapa_map.get(etapa_cod, etapa_cod)
        data_prev = parse_date(cab.get("data_previsao", ""))
        pedido_id = cab.get("codigo_pedido")

        itens = r.get("det", [])
        if not itens:
            # Pedido sem itens — registrar linha com dados do cabeçalho
            vendas.append({
                "pedido_id": pedido_id,
                "valor_mercadorias": valor_merc,
                "etapa": etapa,
                "data_previsao": data_prev,
                "produto_descricao": None,
                "produto_quantidade": None,
                "produto_valor_total": None,
                "sync_timestamp": sync_ts,
                "sync_date": sync_date,
            })
        else:
            for item in itens:
                prod = item.get("produto", {})
                vendas.append({
                    "pedido_id": pedido_id,
                    "valor_mercadorias": valor_merc,
                    "etapa": etapa,
                    "data_previsao": data_prev,
                    "produto_descricao": prod.get("descricao", "Sem nome"),
                    "produto_quantidade": float(prod.get("quantidade", 0) or 0),
                    "produto_valor_total": float(prod.get("valor_total", 0) or 0),
                    "sync_timestamp": sync_ts,
                    "sync_date": sync_date,
                })

    print(f"  ✅ {len(vendas)} linhas de vendas ({len(registros)} pedidos)")
    return vendas


# ============================================================
# SYNC LOG
# ============================================================

def log_sync_start(client: bigquery.Client, sync_id: str, started_at: str) -> None:
    """Registra início do sync no sync_log."""
    row = {
        "sync_id": sync_id,
        "started_at": started_at,
        "status": "running",
        "is_incremental": False,
    }
    load_to_bq(client, "sync_log", [row], "WRITE_APPEND")


def log_sync_success(
    client: bigquery.Client,
    sync_id: str,
    started_at: str,
    counts: dict[str, int],
) -> None:
    """Registra sucesso do sync no sync_log."""
    finished_at = datetime.utcnow().isoformat()
    started_dt = datetime.fromisoformat(started_at)
    finished_dt = datetime.fromisoformat(finished_at)
    duration = int((finished_dt - started_dt).total_seconds())

    row = {
        "sync_id": sync_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": "success",
        "duration_seconds": duration,
        "lancamentos_count": counts.get("lancamentos", 0),
        "saldos_count": counts.get("saldos", 0),
        "clientes_count": counts.get("clientes", 0),
        "projetos_count": counts.get("projetos", 0),
        "categorias_count": counts.get("categorias", 0),
        "error_message": None,
        "is_incremental": False,
    }
    load_to_bq(client, "sync_log", [row], "WRITE_APPEND")


def log_sync_failed(
    client: bigquery.Client,
    sync_id: str,
    started_at: str,
    error_message: str,
) -> None:
    """Registra falha do sync no sync_log."""
    finished_at = datetime.utcnow().isoformat()
    started_dt = datetime.fromisoformat(started_at)
    finished_dt = datetime.fromisoformat(finished_at)
    duration = int((finished_dt - started_dt).total_seconds())

    row = {
        "sync_id": sync_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": "failed",
        "duration_seconds": duration,
        "lancamentos_count": None,
        "saldos_count": None,
        "clientes_count": None,
        "projetos_count": None,
        "categorias_count": None,
        "error_message": str(error_message)[:4000],
        "is_incremental": False,
    }
    load_to_bq(client, "sync_log", [row], "WRITE_APPEND")


def notify_sync_failed(error_message: str) -> None:
    """Envia alerta de falha de sync no Telegram."""
    if not TELEGRAM_BOT_TOKEN or not ADMIN_CHAT_ID:
        return
    try:
        now = datetime.now().strftime("%d/%m/%Y %H:%M")
        msg = f"⚠️ Sync falhou às {now}\n\nErro: {str(error_message)[:500]}\n\nDataset: {GCP_PROJECT_ID}.{BQ_DATASET}"
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_CHAT_ID, "text": msg},
            timeout=10,
        )
        print(f"  📩 Alerta enviado no Telegram (chat {ADMIN_CHAT_ID})")
    except Exception as e:
        print(f"  ⚠ Falha ao enviar alerta Telegram: {e}")


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    t_start = time.time()
    sync_id = str(uuid.uuid4())
    sync_ts = datetime.utcnow().isoformat()
    sync_date = datetime.utcnow().strftime("%Y-%m-%d")

    print("=" * 50)
    print("🔄 OMIE SYNC → BIGQUERY")
    print(f"   {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"   Sync ID: {sync_id}")
    print(f"   Dataset: {GCP_PROJECT_ID}.{BQ_DATASET}")
    print("=" * 50)

    # Validar configuração
    if not OMIE_APP_KEY or not OMIE_APP_SECRET:
        print("\n❌ Configure OMIE_APP_KEY e OMIE_APP_SECRET!")
        sys.exit(1)
    if not GCP_PROJECT_ID:
        print("\n❌ Configure GCP_PROJECT_ID!")
        sys.exit(1)

    # Inicializar BigQuery
    client = get_bq_client()

    # Garantir que tabelas existem (DDL)
    print("\n📋 Verificando tabelas BigQuery...")
    ensure_tables(client)
    ensure_documentos_fiscais_tables(client)

    # Registrar início
    log_sync_start(client, sync_id, sync_ts)

    try:
        # ---- Coleta ----
        cat_map = coletar_categorias()
        proj_map = coletar_projetos()

        saldos, historico = coletar_saldos_bancarios(client, sync_ts, sync_date)

        # Clientes (bulk) — retorna mapa + registros raw
        print("\n📥 Clientes/Fornecedores...", flush=True)
        cli_map, clientes_raw = construir_mapa_clientes_bulk()

        # Movimentos Financeiros (datas reais de pagamento via nCodTitulo)
        mf_datas = coletar_movimentos_financeiros()

        lancamentos = coletar_lancamentos(cat_map, proj_map, cli_map, sync_ts, sync_date, mf_datas)
        clientes_bq = coletar_clientes_bq(clientes_raw, sync_ts)
        vendas = coletar_vendas_bq(sync_ts, sync_date)

        # ---- Proteção contra dados vazios ----
        if not lancamentos and not saldos:
            raise RuntimeError("Nenhum dado coletado (API fora do ar?)")

        # ---- Preparar categorias e projetos para BQ ----
        categorias_bq = [
            {
                "codigo": cod,
                "nome": nome,
                "grupo": ".".join(cod.split(".")[:2]) if cod else None,
                "sync_timestamp": sync_ts,
            }
            for cod, nome in cat_map.items()
        ]

        projetos_bq = [
            {"id": pid, "nome": nome, "sync_timestamp": sync_ts}
            for pid, nome in proj_map.items()
        ]

        # ---- Carregar no BigQuery (MERGE incremental) ----
        print("\n📤 Sincronizando com BigQuery (MERGE)...", flush=True)
        counts: dict[str, int] = {}

        # Lançamentos — MERGE por id
        lanc_cols = ["id", "tipo", "valor", "status", "data_vencimento", "data_emissao",
                     "data_pagamento", "data_previsao", "numero_documento",
                     "categoria_codigo", "categoria_nome", "categoria_grupo",
                     "projeto_id", "projeto_nome", "cliente_id", "cliente_nome",
                     "conta_corrente_id", "is_faturamento_direto", "modalidade", "sync_timestamp", "sync_date"]
        lanc_compare = ["valor", "status", "data_vencimento", "data_pagamento", "data_previsao", "modalidade",
                        "categoria_codigo", "categoria_nome",
                        "projeto_id", "projeto_nome", "cliente_nome"]
        lanc_stats = merge_to_bq(client, "lancamentos", lancamentos, "id", lanc_compare, lanc_cols)
        counts["lancamentos"] = lanc_stats["inserted"] + lanc_stats["updated"]

        # Saldos — TRUNCATE (snapshot D-1, sempre substitui)
        counts["saldos"] = load_to_bq(client, "saldos_bancarios", saldos, "WRITE_TRUNCATE")

        # Categorias — MERGE por codigo
        cat_cols = ["codigo", "nome", "grupo", "sync_timestamp"]
        merge_to_bq(client, "categorias", categorias_bq, "codigo", ["nome", "grupo"], cat_cols)
        counts["categorias"] = len(categorias_bq)

        # Projetos — MERGE por id
        proj_cols = ["id", "nome", "sync_timestamp"]
        merge_to_bq(client, "projetos", projetos_bq, "id", ["nome"], proj_cols)
        counts["projetos"] = len(projetos_bq)

        # Clientes — MERGE por id
        cli_cols = ["id", "nome_fantasia", "razao_social", "estado", "ativo", "pessoa_fisica",
                    "data_cadastro", "sync_timestamp"]
        cli_compare = ["nome_fantasia", "razao_social", "estado", "ativo", "pessoa_fisica"]
        merge_to_bq(client, "clientes", clientes_bq, "id", cli_compare, cli_cols)
        counts["clientes"] = len(clientes_bq)

        # Vendas — TRUNCATE (explode por item, não tem key estável)
        load_to_bq(client, "vendas_pedidos", vendas, "WRITE_TRUNCATE")

        # Histórico — APPEND (dedup via view)
        load_to_bq(client, "historico_saldos", historico, "WRITE_APPEND")

        # TTL: limpar registros > 13 meses para evitar crescimento infinito
        try:
            cleanup_sql = f"""
                DELETE FROM `{table_ref('historico_saldos')}`
                WHERE sync_date < DATE_SUB(CURRENT_DATE(), INTERVAL 13 MONTH)
            """
            result = client.query(cleanup_sql).result()
            deleted = result.num_dml_affected_rows or 0
            if deleted:
                print(f"  🗑 historico_saldos: {deleted} registros antigos removidos (TTL 13 meses)")
        except Exception as e:
            print(f"  ⚠ TTL cleanup falhou: {e}")

        # Documentos Fiscais NF-e — MERGE idempotente, janela 60 dias rolantes
        # Falha isolada: não aborta o sync principal, mas registra o erro com clareza.
        print("\n📥 Documentos Fiscais NF-e (60 dias)...", flush=True)
        try:
            nf_data_ini = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d")
            nf_data_fim = sync_date
            nf_counts = sincronizar_documentos_fiscais(client, nf_data_ini, nf_data_fim)
            counts["documentos_fiscais"] = (
                nf_counts.get("documentos_fiscais", {}).get("inserted", 0)
                + nf_counts.get("documentos_fiscais", {}).get("updated", 0)
            )
        except Exception as nf_err:
            print(f"  ❌ sincronizar_documentos_fiscais falhou: {nf_err}", flush=True)

        # ---- Registrar sucesso ----
        log_sync_success(client, sync_id, sync_ts, counts)

        elapsed = time.time() - t_start
        print(f"\n{'=' * 50}")
        print(f"✅ SYNC CONCLUÍDO COM SUCESSO")
        print(f"   Lançamentos: {counts.get('lancamentos', 0)}")
        print(f"   Saldos: {counts.get('saldos', 0)}")
        print(f"   Histórico: {len(historico)}")
        print(f"   Categorias: {counts.get('categorias', 0)}")
        print(f"   Projetos: {counts.get('projetos', 0)}")
        print(f"   Clientes: {counts.get('clientes', 0)}")
        print(f"   Vendas: {len(vendas)}")
        print(f"   NF-e docs alterados: {counts.get('documentos_fiscais', '❌ falhou')}")
        print(f"   ⏱ Tempo total: {elapsed:.0f}s ({elapsed/60:.1f}min)")
        print("=" * 50)

    except Exception as e:
        elapsed = time.time() - t_start
        print(f"\n❌ ERRO NO SYNC: {e}")
        print(f"   ⏱ Tempo até o erro: {elapsed:.0f}s")
        try:
            log_sync_failed(client, sync_id, sync_ts, str(e))
        except Exception as log_err:
            print(f"  ⚠ Erro ao registrar falha no sync_log: {log_err}")
        notify_sync_failed(str(e))
        sys.exit(1)


# ============================================================
# DOCUMENTOS FISCAIS — NF-e RECEBIDAS (v1)
# Fonte primaria: produtos/nfconsultar/ListarNF (tpNF=0)
# Enriquecimento: produtos/recebimentonfe/ConsultarRecebimento
# NAO plugado no main() — requer aprovacao explicita de Antoine.
# ============================================================

_DDL_SYNC_CHECKPOINTS = """
CREATE TABLE IF NOT EXISTS `{ds}.sync_checkpoints` (
  chave STRING NOT NULL,
  valor STRING,
  atualizado_em TIMESTAMP
)
"""

_DDL_DOCUMENTOS_FISCAIS = """
CREATE TABLE IF NOT EXISTS `{ds}.documentos_fiscais` (
  n_id_receb                INT64   NOT NULL,
  chave_nfe                 STRING,
  n_id_nf                   INT64,
  numero_nfe                STRING,
  serie_nfe                 STRING,
  modelo_nfe                STRING,
  tp_nf                     INT64,
  data_emissao              DATE,
  data_entrada              DATE,
  n_id_destinatario         INT64,
  razao_social_destinatario STRING,
  cnpj_destinatario         STRING,
  n_emit_cod_emp            INT64,
  compl_cod_categ           STRING,
  valor_total_nfe           FLOAT64,
  valor_total_produtos      FLOAT64,
  valor_total_pis           FLOAT64,
  valor_total_cofins        FLOAT64,
  valor_icms                FLOAT64,
  base_icms                 FLOAT64,
  valor_frete               FLOAT64,
  etapa                     STRING,
  faturado                  STRING,
  recebido                  STRING,
  cancelada                 STRING,
  natureza_operacao         STRING,
  origem_dado               STRING,
  synced_at                 TIMESTAMP
)
PARTITION BY data_emissao
OPTIONS (partition_expiration_days = NULL)
CLUSTER BY n_id_destinatario, etapa
"""

_DDL_DOCUMENTOS_FISCAIS_ITENS = """
CREATE TABLE IF NOT EXISTS `{ds}.documentos_fiscais_itens` (
  n_id_receb          INT64   NOT NULL,
  n_sequencia         INT64,
  data_emissao        DATE,
  x_prod              STRING,
  ncm                 STRING,
  cfop                STRING,
  c_prod              STRING,
  c_ean               STRING,
  c_origem            STRING,
  u_com               STRING,
  q_com               FLOAT64,
  v_un_com            FLOAT64,
  v_prod              FLOAT64,
  v_tot_item          FLOAT64,
  v_desc              FLOAT64,
  v_frete             FLOAT64,
  p_icms              FLOAT64,
  v_icms              FLOAT64,
  v_bc_icms           FLOAT64,
  p_pis               FLOAT64,
  v_pis               FLOAT64,
  v_bc_pis            FLOAT64,
  p_cofins            FLOAT64,
  v_cofins            FLOAT64,
  v_bc_cofins         FLOAT64,
  v_bc_st             FLOAT64,
  v_bc_ipi            FLOAT64,
  c_descricao_produto STRING,
  c_codigo_produto    STRING,
  c_unidade_nfe       STRING,
  n_id_produto        INT64,
  n_id_item           INT64,
  c_ignorar_item      STRING,
  categoria_cfop      STRING,
  grupo_ncm           STRING,
  tipo_compra         STRING,
  synced_at           TIMESTAMP
)
PARTITION BY data_emissao
OPTIONS (partition_expiration_days = NULL)
CLUSTER BY n_id_receb, cfop, ncm
"""

_DDL_DOCUMENTOS_FISCAIS_TITULOS = """
CREATE TABLE IF NOT EXISTS `{ds}.documentos_fiscais_titulos` (
  n_id_receb      INT64   NOT NULL,
  n_cod_titulo    INT64   NOT NULL,
  n_parcela       INT64,
  n_cod_tit_repet INT64,
  n_cod_projeto   INT64,
  c_cod_categ     STRING,
  c_doc           STRING,
  c_num_titulo    STRING,
  n_tot_parc      INT64,
  dt_emissao      DATE,
  dt_vencimento   DATE,
  dt_previsao     DATE,
  n_valor_titulo  FLOAT64,
  synced_at       TIMESTAMP
)
PARTITION BY dt_vencimento
OPTIONS (partition_expiration_days = NULL)
CLUSTER BY n_cod_titulo, n_cod_projeto
"""


def ensure_documentos_fiscais_tables(client: bigquery.Client) -> None:
    """Cria tabelas de documentos fiscais e sync_checkpoints se nao existirem."""
    ds = f"{GCP_PROJECT_ID}.{BQ_DATASET}"
    for ddl in [_DDL_SYNC_CHECKPOINTS, _DDL_DOCUMENTOS_FISCAIS,
                _DDL_DOCUMENTOS_FISCAIS_ITENS, _DDL_DOCUMENTOS_FISCAIS_TITULOS]:
        try:
            client.query(ddl.format(ds=ds)).result()
        except Exception as e:
            print(f"  ⚠ ensure_documentos_fiscais_tables falhou: {e}")


def obter_checkpoint(client: bigquery.Client, chave: str) -> Optional[str]:
    """Retorna valor do checkpoint ou None se nao existir / tabela ausente."""
    try:
        sql = (
            f"SELECT valor FROM `{table_ref('sync_checkpoints')}`"
            " WHERE chave = @chave ORDER BY atualizado_em DESC LIMIT 1"
        )
        job = client.query(sql, job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("chave", "STRING", chave)]
        ))
        rows = list(job.result())
        return rows[0].valor if rows else None
    except Exception:
        return None


def salvar_checkpoint(client: bigquery.Client, chave: str, valor: str) -> None:
    """Upserta checkpoint na tabela sync_checkpoints."""
    try:
        sql = (
            f"MERGE `{table_ref('sync_checkpoints')}` T"
            " USING (SELECT @chave AS chave, @valor AS valor,"
            "        CURRENT_TIMESTAMP() AS atualizado_em) S"
            " ON T.chave = S.chave"
            " WHEN MATCHED THEN UPDATE SET valor = S.valor, atualizado_em = S.atualizado_em"
            " WHEN NOT MATCHED THEN INSERT (chave, valor, atualizado_em)"
            "   VALUES (S.chave, S.valor, S.atualizado_em)"
        )
        client.query(sql, job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("chave", "STRING", chave),
                bigquery.ScalarQueryParameter("valor", "STRING", valor),
            ]
        )).result()
    except Exception as e:
        print(f"  ⚠ salvar_checkpoint falhou: {e}")


# ── Classificacao v1 ──────────────────────────────────────────
_CFOP_MAP_V1 = {
    "1101": "compra_industrializacao", "2101": "compra_industrializacao",
    "1102": "compra_revenda",          "2102": "compra_revenda",
    "1116": "compra_industrializacao", "2116": "compra_industrializacao",
    "1117": "compra_industrializacao", "2117": "compra_industrializacao",
    "1201": "devolucao_venda",         "2201": "devolucao_venda",
    "1202": "devolucao_venda",         "2202": "devolucao_venda",
    "1301": "servico_transporte",      "2301": "servico_transporte",
    "1403": "uso_consumo",             "2403": "uso_consumo",
    "1407": "uso_consumo",             "2407": "uso_consumo",
    "1556": "ativo_imobilizado",       "2556": "ativo_imobilizado",
    "1922": "remessa_passagem",        "2922": "remessa_passagem",
    "1949": "outros",                  "2949": "outros",
}

_NCM_MAP_V1 = {
    "44": "madeira",             "48": "papel_papelao",
    "39": "plasticos",           "63": "texteis_confeccionados",
    "68": "ceramica_pedra",      "69": "ceramica_pedra",
    "73": "ferro_aco_metais",    "84": "maquinas_equipamentos",
    "85": "eletrico_eletronico", "94": "moveis_iluminacao",
}


def _classificar_cfop(cfop: str) -> str:
    """Retorna categoria_cfop a partir do CFOP do destinatario."""
    c = str(cfop or "").replace(".", "").strip()
    if not c:
        return "outros"
    if c.startswith("3"):
        return "importacao"
    return _CFOP_MAP_V1.get(c, "outros")


def _classificar_ncm(ncm: str) -> str:
    """Retorna grupo_ncm a partir dos 2 primeiros digitos do NCM."""
    digits = str(ncm or "").replace(".", "").strip()
    return _NCM_MAP_V1.get(digits[:2], "outros") if digits else "outros"


def _classificar_tipo_compra(cfop: str, ncm: str) -> tuple:
    """Retorna (categoria_cfop, grupo_ncm, tipo_compra)."""
    cc = _classificar_cfop(cfop)
    gn = _classificar_ncm(ncm)
    if cc == "devolucao_venda":   return cc, gn, "devolucao"
    if cc == "ativo_imobilizado": return cc, gn, "ativo_imobilizado"
    if cc == "importacao":        return cc, gn, "importacao"
    if cc == "uso_consumo":       return cc, gn, "uso_consumo_operacional"
    if cc == "remessa_passagem":  return cc, gn, "remessa_passagem"
    if cc == "compra_revenda":
        if gn in ("moveis_iluminacao", "texteis_confeccionados",
                  "plasticos", "ceramica_pedra", "madeira"):
            return cc, gn, "material_projeto"
        if gn == "ferro_aco_metais":
            return cc, gn, "ferragem_obra"
        if gn in ("eletrico_eletronico", "maquinas_equipamentos"):
            return cc, gn, "equipamento_projeto"
        return cc, gn, "mercadoria_geral"
    return cc, gn, "nao_classificado"


# ── Normalizadores ────────────────────────────────────────────
def _normalizar_documento_fiscal(nf_obj: dict, rec_obj: dict, now: str) -> dict:
    """
    Normaliza um registro de NF-e para documentos_fiscais.
    nf_obj: registro de nfconsultar (compl, ide, nfDestInt, nfEmitInt, total...)
    rec_obj: response de ConsultarRecebimento (cabec, totais, infoCadastro) ou {}
    """
    compl    = nf_obj.get("compl", {})
    ide      = nf_obj.get("ide", {})
    dest     = nf_obj.get("nfDestInt", {})
    emit     = nf_obj.get("nfEmitInt", {})
    cabec_r  = rec_obj.get("cabec", {})
    totais_r = rec_obj.get("totais", {})
    info_r   = rec_obj.get("infoCadastro", {})

    return {
        "n_id_receb":                compl.get("nIdReceb"),
        "chave_nfe":                 compl.get("cChaveNFe"),
        "n_id_nf":                   compl.get("nIdNF"),
        "numero_nfe":                cabec_r.get("cNumeroNFe") or ide.get("nNF"),
        "serie_nfe":                 cabec_r.get("cSerieNFe") or ide.get("serie"),
        "modelo_nfe":                cabec_r.get("cModeloNFe") or ide.get("mod"),
        "tp_nf":                     ide.get("tpNF", 0),
        "data_emissao":              parse_date(cabec_r.get("dEmissaoNFe") or ide.get("dEmi")),
        "data_entrada":              parse_date(ide.get("dSaiEnt")),
        # Destinatario = Studio Koti (quem recebeu a NF-e)
        "n_id_destinatario":         cabec_r.get("nIdFornecedor") or dest.get("nCodCli"),
        "razao_social_destinatario": cabec_r.get("cRazaoSocial") or dest.get("cRazao"),
        "cnpj_destinatario":         cabec_r.get("cCNPJ_CPF") or dest.get("cnpj_cpf"),
        # Emitente externo (fornecedor real) — somente ID interno Omie
        "n_emit_cod_emp":            emit.get("nCodEmp"),
        "compl_cod_categ":           compl.get("cCodCateg"),
        "valor_total_nfe":           cabec_r.get("nValorNFe") or nf_obj.get("total", {}).get("vNF"),
        "valor_total_produtos":      totais_r.get("vTotalProdutos"),
        "valor_total_pis":           totais_r.get("vTotalPIS"),
        "valor_total_cofins":        totais_r.get("vTotalCOFINS"),
        "valor_icms":                totais_r.get("vICMS"),
        "base_icms":                 totais_r.get("bcICMS"),
        "valor_frete":               totais_r.get("vFrete"),
        "etapa":                     cabec_r.get("cEtapa"),
        "faturado":                  info_r.get("cFaturado"),
        "recebido":                  info_r.get("cRecebido"),
        "cancelada":                 info_r.get("cCancelada"),
        "natureza_operacao":         cabec_r.get("cNaturezaOperacao"),
        "origem_dado":               "nfconsultar+recebimentonfe",
        "synced_at":                 now,
    }


def _normalizar_item_fiscal(
    n_id_receb: int, det: dict, enrich: dict, data_emissao: Optional[str], now: str,
    seq: Optional[int] = None,
) -> dict:
    """
    Normaliza um item de nfconsultar.det[] para documentos_fiscais_itens.
    enrich: itensCabec correspondente de recebimentonfe (ou {}).
    seq: posição 1-based do item no array det[] (via enumerate).
    """
    prod = det.get("prod", {})
    cfop = prod.get("CFOP", "")
    ncm  = prod.get("NCM", "")
    cc, gn, tc = _classificar_tipo_compra(cfop, ncm)

    return {
        "n_id_receb":          n_id_receb,
        "n_sequencia":         seq,
        "data_emissao":        data_emissao,
        "x_prod":              prod.get("xProd"),
        "ncm":                 ncm,
        "cfop":                cfop,
        "c_prod":              prod.get("cProd"),
        "c_ean":               prod.get("cEAN"),
        "c_origem":            prod.get("cOrigem"),
        "u_com":               prod.get("uCom"),
        "q_com":               prod.get("qCom"),
        "v_un_com":            prod.get("vUnCom"),
        "v_prod":              prod.get("vProd"),
        "v_tot_item":          prod.get("vTotItem"),
        "v_desc":              prod.get("vDesc"),
        "v_frete":             prod.get("vFrete"),
        "p_icms":              prod.get("pICMS"),
        "v_icms":              prod.get("vICMS"),
        "v_bc_icms":           prod.get("vBC"),
        "p_pis":               prod.get("pPIS"),
        "v_pis":               prod.get("vPIS"),
        "v_bc_pis":            prod.get("vBCPIS"),
        "p_cofins":            prod.get("pCOFINS"),
        "v_cofins":            prod.get("vCOFINS"),
        "v_bc_cofins":         prod.get("vBCCOFINS"),
        "v_bc_st":             prod.get("vBCST"),
        "v_bc_ipi":            prod.get("vBCIPI"),
        "c_descricao_produto": enrich.get("cDescricaoProduto"),
        "c_codigo_produto":    enrich.get("cCodigoProduto"),
        "c_unidade_nfe":       enrich.get("cUnidadeNfe"),
        "n_id_produto":        enrich.get("nIdProduto"),
        "n_id_item":           enrich.get("nIdItem"),
        "c_ignorar_item":      enrich.get("cIgnorarItem"),
        "categoria_cfop":      cc,
        "grupo_ncm":           gn,
        "tipo_compra":         tc,
        "synced_at":           now,
    }


def _normalizar_titulo_fiscal(n_id_receb: int, tit: dict, now: str) -> dict:
    """Normaliza um titulo de nfconsultar.titulos[] para documentos_fiscais_titulos."""
    return {
        "n_id_receb":      n_id_receb,
        "n_cod_titulo":    tit.get("nCodTitulo"),    # link com lancamentos.id
        "n_parcela":       tit.get("nParcela"),
        "n_cod_tit_repet": tit.get("nCodTitRepet"),
        "n_cod_projeto":   tit.get("nCodProjeto"),
        "c_cod_categ":     tit.get("cCodCateg"),
        "c_doc":           tit.get("cDoc"),
        "c_num_titulo":    tit.get("cNumTitulo"),
        "n_tot_parc":      tit.get("nTotParc"),
        "dt_emissao":      parse_date(tit.get("dDtEmissao")),
        "dt_vencimento":   parse_date(tit.get("dDtVenc")),
        "dt_previsao":     parse_date(tit.get("dDtPrevisao")),
        "n_valor_titulo":  tit.get("nValorTitulo"),
        "synced_at":       now,
    }


# ── Coletor (sem escrita BQ) ──────────────────────────────────
def coletar_documentos_fiscais(
    data_ini: str, data_fim: str, limite: Optional[int] = None
) -> tuple:
    """
    Coleta NF-e recebidas no periodo [data_ini, data_fim] (YYYY-MM-DD).
    Retorna (documentos, itens, titulos) — listas de dicts normalizados.
    NAO escreve em BigQuery.
    """
    def _omie_fmt(d: str) -> str:
        if d and len(d) == 10 and d[4] == "-":
            return f"{d[8:10]}/{d[5:7]}/{d[0:4]}"
        return d

    d_ini = _omie_fmt(data_ini)
    d_fim = _omie_fmt(data_fim)

    print(f"\n📥 Documentos fiscais {data_ini} → {data_fim}...", flush=True)

    nfs = paginar(
        "produtos/nfconsultar", "ListarNF",
        {"tpNF": 0, "dEmiInicial": d_ini, "dEmiFinal": d_fim},
        lista_key="nfCadastro",
    )
    print(f"  ListarNF: {len(nfs)} documentos", flush=True)

    if limite:
        nfs = nfs[:limite]

    # Enriquecimento via ConsultarRecebimento
    receb_cache: dict = {}
    sem_nidreceb = 0
    for nf in nfs:
        nid = nf.get("compl", {}).get("nIdReceb")
        if not nid:
            sem_nidreceb += 1
            continue
        if nid in receb_cache:
            continue
        det = omie_request("produtos/recebimentonfe", "ConsultarRecebimento",
                           {"nIdReceb": nid})
        if det and "cabec" in det:
            receb_cache[nid] = det
        time.sleep(0.1)

    if sem_nidreceb:
        print(f"  Aviso: {sem_nidreceb} NFs sem nIdReceb", flush=True)
    print(f"  ConsultarRecebimento: {len(receb_cache)} detalhes", flush=True)

    now = datetime.utcnow().isoformat()
    documentos: list = []
    itens: list = []
    titulos: list = []

    for nf in nfs:
        n_id_receb = nf.get("compl", {}).get("nIdReceb")
        rec        = receb_cache.get(n_id_receb, {})

        row_doc = _normalizar_documento_fiscal(nf, rec, now)
        documentos.append(row_doc)
        data_emissao = row_doc["data_emissao"]

        # Itens — join por nfProdInt.nCodItem = itensCabec.nIdItem; sequência positional
        receb_by_cod = {
            it.get("itensCabec", {}).get("nIdItem"): it.get("itensCabec", {})
            for it in rec.get("itensRecebimento", [])
        }
        for seq, det in enumerate(nf.get("det", []), start=1):
            n_cod_item = det.get("nfProdInt", {}).get("nCodItem")
            enrich = receb_by_cod.get(n_cod_item, {})
            itens.append(_normalizar_item_fiscal(n_id_receb, det, enrich, data_emissao, now, seq=seq))

        # Titulos — somente os que tem link CP
        for tit in nf.get("titulos", []):
            if tit.get("nCodTitulo"):
                titulos.append(_normalizar_titulo_fiscal(n_id_receb, tit, now))

    print(
        f"  Normalizado: {len(documentos)} docs, {len(itens)} itens, {len(titulos)} titulos",
        flush=True,
    )

    # Deduplica por chave primária (API Omie pode retornar o mesmo registro em páginas sobrepostas)
    # Critério: manter primeira ocorrência (registros duplicados são idênticos na prática)
    def _dedup(rows: list, key_fn) -> tuple[list, int]:
        seen: set = set()
        out: list = []
        for row in rows:
            k = key_fn(row)
            if k not in seen:
                seen.add(k)
                out.append(row)
        return out, len(rows) - len(out)

    documentos, n_dup_docs = _dedup(documentos, lambda r: r.get("n_id_receb"))
    itens,      n_dup_itens = _dedup(itens, lambda r: (r.get("n_id_receb"), r.get("n_sequencia")))
    titulos,    n_dup_tit   = _dedup(titulos, lambda r: r.get("n_cod_titulo"))

    if n_dup_docs or n_dup_itens or n_dup_tit:
        print(
            f"  ⚠ Deduplicados (API): {n_dup_docs} docs, {n_dup_itens} itens, {n_dup_tit} titulos",
            flush=True,
        )

    return documentos, itens, titulos


# ── Sincronizador BigQuery ─────────────────────────────────────
# NAO chamado pelo main() — requer aprovacao explicita de Antoine.
def sincronizar_documentos_fiscais(
    client: bigquery.Client, data_ini: str, data_fim: str
) -> dict:
    """
    Sincroniza NF-e recebidas para BigQuery via MERGE.
    Janela recomendada: 60 dias rolantes.
    Requer ensure_documentos_fiscais_tables() antes da primeira execucao.
    """
    documentos, itens, titulos = coletar_documentos_fiscais(data_ini, data_fim)

    # Validação prévia: n_sequencia deve ser preenchido em todos os itens
    if any(it.get("n_sequencia") is None for it in itens):
        nulls = [it.get("n_id_receb") for it in itens if it.get("n_sequencia") is None]
        raise ValueError(f"n_sequencia NULL em {len(nulls)} itens (primeiros: {nulls[:5]})")

    counts: dict = {}

    # documentos_fiscais — MERGE por n_id_receb
    doc_cols = [
        "n_id_receb", "chave_nfe", "n_id_nf", "numero_nfe", "serie_nfe", "modelo_nfe",
        "tp_nf", "data_emissao", "data_entrada", "n_id_destinatario",
        "razao_social_destinatario", "cnpj_destinatario", "n_emit_cod_emp",
        "compl_cod_categ", "valor_total_nfe", "valor_total_produtos",
        "valor_total_pis", "valor_total_cofins", "valor_icms", "base_icms",
        "valor_frete", "etapa", "faturado", "recebido", "cancelada",
        "natureza_operacao", "origem_dado", "synced_at",
    ]
    doc_compare = ["etapa", "faturado", "recebido", "cancelada", "valor_total_nfe"]
    counts["documentos_fiscais"] = merge_to_bq(
        client, "documentos_fiscais", documentos, "n_id_receb", doc_compare, doc_cols,
        no_delete=True,
    )

    # documentos_fiscais_itens — MERGE idempotente por chave composta (n_id_receb, n_sequencia)
    item_cols = [
        "n_id_receb", "n_sequencia", "data_emissao",
        "x_prod", "ncm", "cfop", "c_prod", "c_ean", "c_origem", "u_com",
        "q_com", "v_un_com", "v_prod", "v_tot_item", "v_desc", "v_frete",
        "p_icms", "v_icms", "v_bc_icms", "p_pis", "v_pis", "v_bc_pis",
        "p_cofins", "v_cofins", "v_bc_cofins", "v_bc_st", "v_bc_ipi",
        "c_descricao_produto", "c_codigo_produto", "c_unidade_nfe",
        "n_id_produto", "n_id_item", "c_ignorar_item",
        "categoria_cfop", "grupo_ncm", "tipo_compra", "synced_at",
    ]
    item_compare = ["x_prod", "q_com", "v_prod", "tipo_compra"]
    counts["documentos_fiscais_itens"] = merge_to_bq(
        client, "documentos_fiscais_itens", itens,
        ["n_id_receb", "n_sequencia"], item_compare, item_cols,
        no_delete=True,
    )

    # documentos_fiscais_titulos — MERGE por n_cod_titulo (link unico com lancamentos)
    tit_cols = [
        "n_id_receb", "n_cod_titulo", "n_parcela", "n_cod_tit_repet",
        "n_cod_projeto", "c_cod_categ", "c_doc", "c_num_titulo", "n_tot_parc",
        "dt_emissao", "dt_vencimento", "dt_previsao", "n_valor_titulo", "synced_at",
    ]
    tit_compare = ["n_cod_projeto", "dt_vencimento", "n_valor_titulo"]
    counts["documentos_fiscais_titulos"] = merge_to_bq(
        client, "documentos_fiscais_titulos", titulos, "n_cod_titulo", tit_compare, tit_cols,
        no_delete=True,
    )

    salvar_checkpoint(client, "documentos_fiscais_ultima_fim", data_fim)
    return counts


if __name__ == "__main__":
    main()
