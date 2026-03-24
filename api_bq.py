#!/usr/bin/env python3
"""
API que serve dados do BigQuery no formato JSON esperado pelo dashboard_omie.html.

Pode rodar como:
  1. Cloud Function (GCP) — deploy via gcloud
  2. Servidor local — python api_bq.py (porta 8080)

O JSON retornado é idêntico ao formato do omie_sync.py (dados_omie.json),
permitindo que o dashboard HTML funcione sem alterações de lógica.

Variáveis de ambiente:
  GCP_PROJECT_ID                 — projeto GCP (ex: dashboard-koti-omie)
  BQ_DATASET                     — dataset BigQuery (default: studio_koti)
  GOOGLE_APPLICATION_CREDENTIALS — path para JSON da service account (local)
  ALLOWED_ORIGINS                — origins permitidos para CORS (Cloud Function)
"""

import json
import os
from datetime import datetime, timezone, timedelta

BRT = timezone(timedelta(hours=-3))
from collections import defaultdict

from google.cloud import bigquery

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "dashboard-koti-omie")
BQ_DATASET = os.environ.get("BQ_DATASET", "studio_koti")
ALLOWED_ORIGINS = [
    "https://akliot.github.io",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]

_client = None


def get_client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(project=GCP_PROJECT_ID)
    return _client


def tbl(name: str) -> str:
    return f"`{GCP_PROJECT_ID}.{BQ_DATASET}.{name}`"


def date_to_ddmmyyyy(d) -> str:
    """Converte date/datetime/string para DD/MM/YYYY."""
    if d is None:
        return ""
    if hasattr(d, "strftime"):
        return d.strftime("%d/%m/%Y")
    return str(d)


def date_to_ymd(d) -> str:
    """Converte date/datetime para YYYY-MM-DD."""
    if d is None:
        return ""
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    return str(d)


def query_rows(sql: str) -> list[dict]:
    """Executa query e retorna lista de dicts."""
    client = get_client()
    return [dict(row) for row in client.query(sql).result()]


def build_json() -> dict:
    """Constrói o JSON completo no formato esperado pelo dashboard HTML."""

    now = datetime.now(BRT)

    # ---- Lançamentos ----
    rows = query_rows(f"""
        SELECT id, tipo, valor, status, data_vencimento, data_emissao,
               data_pagamento, data_previsao, numero_documento, categoria_codigo, categoria_nome,
               projeto_id, projeto_nome, cliente_id, cliente_nome,
               is_faturamento_direto, modalidade
        FROM {tbl('lancamentos')}
    """)
    lancamentos = []
    proj_ids_com_mov = set()
    for r in rows:
        proj_id = r.get("projeto_id")
        if proj_id:
            proj_ids_com_mov.add(proj_id)
        status = (r.get("status", "") or "").upper()
        # PAGO/RECEBIDO → data_pagamento; pendentes → data_previsao (dia útil real)
        if status in ("PAGO", "RECEBIDO", "LIQUIDADO") and r.get("data_pagamento"):
            data_ref = r.get("data_pagamento")
        else:
            data_ref = r.get("data_previsao") or r.get("data_vencimento")
        lancamentos.append({
            "id": r["id"],
            "valor": float(r.get("valor", 0) or 0),
            "tipo": r.get("tipo", ""),
            "status": status,
            "data": date_to_ddmmyyyy(data_ref),
            "data_vencimento": date_to_ddmmyyyy(r.get("data_vencimento")),
            "data_pagamento": date_to_ddmmyyyy(r.get("data_pagamento")),
            "data_previsao": date_to_ddmmyyyy(r.get("data_previsao")),
            "data_emissao": date_to_ddmmyyyy(r.get("data_emissao")),
            "categoria": r.get("categoria_codigo") or "",
            "categoria_nome": r.get("categoria_nome", ""),
            "projeto": proj_id,
            "projeto_nome": r.get("projeto_nome", "Sem projeto") or "Sem projeto",
            "cliente_nome": r.get("cliente_nome", "") or "",
            "is_fd": bool(r.get("is_faturamento_direto")),
            "modalidade": r.get("modalidade") or ("FD" if r.get("is_faturamento_direto") else "SK"),
        })

    # ---- Categorias (mapa código→nome) ----
    cat_rows = query_rows(f"SELECT codigo, nome FROM {tbl('categorias')}")
    categorias = {r["codigo"]: r["nome"] for r in cat_rows}

    # ---- Projetos (apenas com movimentação) ----
    proj_rows = query_rows(f"SELECT id, nome FROM {tbl('projetos')} ORDER BY nome")
    projetos = [
        {"id": r["id"], "nome": r["nome"]}
        for r in proj_rows
        if r["id"] in proj_ids_com_mov
    ]

    # ---- Saldos bancários ----
    saldo_rows = query_rows(f"""
        SELECT conta_id, conta_nome, conta_tipo, saldo, saldo_conciliado,
               diferenca, data_referencia
        FROM {tbl('saldos_bancarios')}
    """)
    saldos_bancarios = [
        {
            "id": r["conta_id"],
            "nome": r.get("conta_nome", ""),
            "tipo": r.get("conta_tipo", ""),
            "saldo": float(r.get("saldo", 0) or 0),
            "saldo_conciliado": float(r.get("saldo_conciliado", 0) or 0),
            "diferenca": float(r.get("diferenca", 0) or 0),
            "data": date_to_ddmmyyyy(r.get("data_referencia")),
        }
        for r in saldo_rows
    ]

    # ---- Histórico de conciliação (via view dedup) ----
    hist_rows = query_rows(f"""
        SELECT conta_id, conta_nome, data_referencia, label,
               saldo_atual, saldo_conciliado, diferenca, tipo
        FROM {tbl('v_historico_saldos')}
        ORDER BY data_referencia
    """)
    historico_conciliacao = [
        {
            "banco_id": r["conta_id"],
            "banco_nome": r.get("conta_nome", ""),
            "data": date_to_ymd(r.get("data_referencia")),
            "label": r.get("label", ""),
            "saldo_atual": float(r.get("saldo_atual", 0) or 0),
            "saldo_conciliado": float(r.get("saldo_conciliado", 0) or 0),
            "diferenca": float(r.get("diferenca", 0) or 0),
            "tipo": r.get("tipo", "mensal"),
        }
        for r in hist_rows
    ]

    # ---- Vendas (resumo agregado como o omie_sync.py original) ----
    venda_rows = query_rows(f"""
        SELECT pedido_id, valor_mercadorias, etapa, data_previsao,
               produto_descricao, produto_quantidade, produto_valor_total
        FROM {tbl('vendas_pedidos')}
    """)

    # Agregar por pedido (dedup)
    pedidos_vistos = set()
    total_vendas = 0.0
    por_mes: dict[str, dict] = {}
    por_etapa: dict[str, int] = defaultdict(int)
    top_produtos: dict[str, dict] = {}

    for r in venda_rows:
        pid = r.get("pedido_id")
        valor = float(r.get("valor_mercadorias", 0) or 0)
        etapa = r.get("etapa", "")
        data_prev = r.get("data_previsao")

        # Contagem por pedido (não duplicar)
        if pid not in pedidos_vistos:
            pedidos_vistos.add(pid)
            total_vendas += valor
            por_etapa[etapa] += 1
            if data_prev and hasattr(data_prev, "strftime"):
                mes = data_prev.strftime("%Y-%m")
                if mes not in por_mes:
                    por_mes[mes] = {"valor": 0, "qtd": 0}
                por_mes[mes]["valor"] += valor
                por_mes[mes]["qtd"] += 1

        # Produtos
        desc = r.get("produto_descricao", "Sem nome") or "Sem nome"
        qtd = float(r.get("produto_quantidade", 0) or 0)
        val = float(r.get("produto_valor_total", 0) or 0)
        if desc not in top_produtos:
            top_produtos[desc] = {"qtd": 0, "valor": 0}
        top_produtos[desc]["qtd"] += qtd
        top_produtos[desc]["valor"] += val

    qtd_pedidos = len(pedidos_vistos)
    top_prod_sorted = dict(sorted(top_produtos.items(), key=lambda x: x[1]["valor"], reverse=True)[:10])

    vendas = {
        "total_vendas": total_vendas,
        "quantidade_pedidos": qtd_pedidos,
        "ticket_medio": total_vendas / qtd_pedidos if qtd_pedidos > 0 else 0,
        "por_mes": dict(por_mes),
        "por_etapa": dict(por_etapa),
        "top_produtos": top_prod_sorted,
    }

    # ---- Clientes (resumo agregado) ----
    cli_rows = query_rows(f"""
        SELECT id, nome_fantasia, estado, ativo, pessoa_fisica, data_cadastro
        FROM {tbl('clientes')}
    """)
    por_estado: dict[str, int] = defaultdict(int)
    por_mes_cadastro: dict[str, int] = defaultdict(int)
    ativos = 0
    inativos = 0
    pf = 0
    pj = 0

    for r in cli_rows:
        estado = r.get("estado") or "N/I"
        por_estado[estado] += 1
        if r.get("ativo"):
            ativos += 1
        else:
            inativos += 1
        if r.get("pessoa_fisica"):
            pf += 1
        else:
            pj += 1
        dt = r.get("data_cadastro")
        if dt and hasattr(dt, "strftime"):
            por_mes_cadastro[dt.strftime("%Y-%m")] += 1

    # Top 15 estados
    top_estados = dict(sorted(por_estado.items(), key=lambda x: x[1], reverse=True)[:15])

    clientes = {
        "total_clientes": len(cli_rows),
        "ativos": ativos,
        "inativos": inativos,
        "pessoa_fisica": pf,
        "pessoa_juridica": pj,
        "por_estado": top_estados,
        "por_mes_cadastro": dict(por_mes_cadastro),
    }

    # ---- Orçamento DRE (formato esperado pelo HTML: dre[], meses_disponiveis, meses_com_real) ----
    orc_rows = query_rows(f"""
        SELECT label, section, level, mes, valor_real, valor_bp, mes_com_real
        FROM {tbl('orcamento_dre')}
        ORDER BY label, mes
    """)

    orcamento = None
    if orc_rows:
        # Agrupar por (label, section, level) → dicts de bp e real por mês
        dre_map: dict[str, dict] = {}
        all_meses: set[str] = set()
        meses_com_real: set[str] = set()

        for r in orc_rows:
            key = r["label"]
            mes = r.get("mes", "")
            all_meses.add(mes)
            if r.get("mes_com_real"):
                meses_com_real.add(mes)

            if key not in dre_map:
                dre_map[key] = {
                    "label": r["label"],
                    "section": r.get("section", ""),
                    "level": int(r.get("level", 0)),
                    "bp": {},
                    "real": {},
                }
            dre_map[key]["bp"][mes] = float(r.get("valor_bp", 0) or 0)
            dre_map[key]["real"][mes] = float(r.get("valor_real", 0) or 0)

        # Ordem das linhas DRE (mesma do extract_bp_bq.py)
        dre_order = [
            "Receita Bruta", "SK", "BK", "RT", "Aditivo", "Vendas RP",
            "Impostos", "ICMS", "Crédito de ICMS", "ISS", "PIS/COFINS",
            "Receita Líquida",
            "Custos Operacionais", "Comissões Externas", "Comissões Internas", "Obras (Total)",
            "Margem de Contribuição",
            "Despesas Gerais e Adm", "Salários e Encargos", "Despesas Administrativas",
            "Despesas Comerciais", "Despesas com Imóvel", "Despesas com Veículos", "Despesas com Diretoria",
            "EBITDA", "Receitas/Despesas Financeiras", "IRPJ/CSLL", "Lucro Líquido",
        ]
        order_idx = {label: i for i, label in enumerate(dre_order)}
        dre_list = sorted(dre_map.values(), key=lambda d: order_idx.get(d["label"], 99))

        orcamento = {
            "meses_disponiveis": sorted(all_meses),
            "meses_com_real": sorted(meses_com_real),
            "dre": dre_list,
        }

    result = {
        "atualizado_em": now.isoformat(),
        "atualizado_em_formatado": now.strftime("%d/%m/%Y às %H:%M"),
        "lancamentos": lancamentos,
        "categorias": categorias,
        "projetos": projetos,
        "saldos_bancarios": saldos_bancarios,
        "historico_conciliacao": historico_conciliacao,
        "vendas": vendas,
        "clientes": clientes,
    }

    # Incluir orçamento se disponível
    if orcamento:
        result["orcamento"] = orcamento

    return result


# ============================================================
# CLOUD FUNCTION ENTRY POINT
# ============================================================

def _cors_origin(request):
    """Retorna origin permitido para CORS."""
    origin = request.headers.get("Origin", "")
    if origin in ALLOWED_ORIGINS:
        return origin
    return ALLOWED_ORIGINS[0]


def api_dashboard(request):
    """Entry point para Cloud Function (HTTP trigger)."""
    cors = _cors_origin(request)

    if request.method == "OPTIONS":
        headers = {
            "Access-Control-Allow-Origin": cors,
            "Access-Control-Allow-Methods": "GET",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "3600",
        }
        return ("", 204, headers)

    headers = {
        "Access-Control-Allow-Origin": cors,
        "Content-Type": "application/json",
        "Cache-Control": "public, max-age=300",
    }

    try:
        data = build_json()
        return (json.dumps(data, ensure_ascii=False), 200, headers)
    except Exception as e:
        return (json.dumps({"error": str(e)}), 500, headers)


# ============================================================
# LOCAL SERVER (para dev)
# ============================================================

if __name__ == "__main__":
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import sys

    PORT = int(os.environ.get("PORT", 8080))

    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                # Servir dashboard_bq.html
                html_path = os.path.join(SCRIPT_DIR, "dashboard_bq.html")
                try:
                    with open(html_path, "r", encoding="utf-8") as f:
                        body = f.read().encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body)
                except FileNotFoundError:
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"dashboard_bq.html not found")
            elif self.path in ("/api", "/api/dashboard"):
                # Servir dados JSON do BigQuery
                try:
                    data = build_json()
                    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "public, max-age=300")
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as e:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(e)}).encode())
            elif self.path.endswith(".json") and not self.path.startswith("/api"):
                # Servir arquivos JSON estáticos (rh_data.json, etc.)
                file_path = os.path.join(SCRIPT_DIR, self.path.lstrip("/"))
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        body = f.read().encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(body)
                except FileNotFoundError:
                    self.send_response(404)
                    self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def log_message(self, format, *args):
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}", flush=True)

    print(f"🚀 API Dashboard rodando em http://localhost:{PORT}")
    print(f"   Projeto: {GCP_PROJECT_ID} / Dataset: {BQ_DATASET}")
    HTTPServer(("", PORT), Handler).serve_forever()
