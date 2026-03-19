#!/usr/bin/env python3
"""
Omie API Data Sync Script v6 - INCREMENTAL
Coleta dados detalhados (com categorias e projetos) da API Omie
e salva em JSON para consumo pelo dashboard.

v6: Sync incremental — cacheia mapa de clientes e categorias,
    só busca nomes novos. Reduz sync de ~20min para ~3-4min.

Uso:
  1. Configure OMIE_APP_KEY e OMIE_APP_SECRET abaixo (ou via env vars)
  2. pip install requests
  3. python3 omie_sync.py
  4. Abra dashboard_omie.html no navegador (carrega dados_omie.json automaticamente)

Flags:
  --full    Força sync completo (ignora cache)
"""

import json
import requests
from datetime import datetime, timedelta
import os
import sys
import time

# ============================================================
# CONFIGURAÇÃO
# ============================================================
OMIE_APP_KEY = os.environ.get("OMIE_APP_KEY", "5783428549899")
OMIE_APP_SECRET = os.environ.get("OMIE_APP_SECRET", "b92ec385c87d8fd50f782826cf078501")

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "dados_omie.json")

BASE_URL = "https://app.omie.com.br/api/v1"

# Contas correntes a IGNORAR (não representam movimentação real)
CONTAS_IGNORAR = {
    8754849088,  # BAIXA DE NFS - conta fictícia para baixa de notas
}

# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def omie_request(endpoint, call, params, retries=3):
    url = f"{BASE_URL}/{endpoint}/"
    payload = {
        "call": call,
        "app_key": OMIE_APP_KEY,
        "app_secret": OMIE_APP_SECRET,
        "param": [params]
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


def paginar(endpoint, call, param_base, lista_key, max_pages=200):
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


def carregar_cache():
    """Carrega dados existentes para uso como cache."""
    if not os.path.exists(OUTPUT_FILE):
        print("  📄 Sem arquivo anterior — sync completo")
        return None
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            dados = json.load(f)
        ultimo = dados.get("atualizado_em", "")
        print(f"  📄 Cache encontrado (atualizado: {dados.get('atualizado_em_formatado', ultimo)})")
        return dados
    except Exception as e:
        print(f"  ⚠ Erro lendo cache: {e} — sync completo")
        return None


# ============================================================
# COLETA DE DADOS
# ============================================================

def _limpar_nome_categoria(desc):
    """Remove prefixo numérico tipo '2.01.03 - ' do nome da categoria."""
    import re
    return re.sub(r'^\d+(\.\d+)+ - ', '', desc).strip()


def coletar_categorias(cache_cat_map=None):
    print("\n📥 Categorias...", flush=True)
    data = omie_request("geral/categorias", "ListarCategorias", {"pagina": 1, "registros_por_pagina": 500})
    cat_map = {}
    if cache_cat_map:
        cat_map.update(cache_cat_map)
        print(f"  📦 {len(cache_cat_map)} categorias do cache")
    if data:
        for c in data.get("categoria_cadastro", []):
            cod = c.get("codigo", "")
            desc = c.get("descricao", "")
            if "<Disponível>" not in desc and desc:
                cat_map[cod] = _limpar_nome_categoria(desc)
    print(f"  ✅ {len(cat_map)} categorias total")
    return cat_map


def completar_categorias(cat_map, lancamentos):
    """Busca nomes via ConsultarCategoria para códigos usados nos lançamentos
    que não vieram no ListarCategorias."""
    codigos_usados = set()
    for l in lancamentos:
        cat = l.get("categoria") or ""
        if cat:
            codigos_usados.add(cat)
            codigos_usados.add(".".join(cat.split(".")[:2]))  # grupo

    faltantes = [c for c in sorted(codigos_usados) if c and c not in cat_map]
    if not faltantes:
        print(f"  ✅ Todas categorias já conhecidas")
        return cat_map

    print(f"  🔍 Buscando {len(faltantes)} categorias faltantes via ConsultarCategoria...")
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
    print(f"  ✅ {encontrados} categorias adicionais encontradas (total: {len(cat_map)})")
    return cat_map


def coletar_projetos():
    print("\n📥 Projetos...", flush=True)
    registros = paginar("geral/projetos", "ListarProjetos", {}, "cadastro")
    proj_map = {}
    for p in registros:
        pid = p.get("codigo")
        nome = p.get("nome", "")
        if pid and nome:
            proj_map[pid] = nome
    print(f"  ✅ {len(proj_map)} projetos")
    return proj_map


def _extrato_snapshot(cc_id, d_ini, d_fim):
    """Busca saldos de uma conta em um período.
    Retorna:
      - saldo: nSaldoAtual (= coluna "Saldo" no Omie)
      - saldo_conciliado: nSaldoConciliado
    """
    ext = omie_request("financas/extrato", "ListarExtrato", {
        "nCodCC": cc_id,
        "dPeriodoInicial": d_ini,
        "dPeriodoFinal": d_fim
    })
    if not ext:
        return None

    return {
        "saldo": float(ext.get("nSaldoAtual", 0) or 0),
        "saldo_conciliado": float(ext.get("nSaldoConciliado", 0) or 0),
    }


def coletar_saldos_bancarios(cache_historico=None):
    """Coleta saldos bancários com dados de conciliação + histórico mensal e diário.
    Reutiliza histórico de meses passados do cache (não mudam)."""
    print("\n📥 Saldos Bancários + Conciliação...", flush=True)
    data = omie_request("geral/contacorrente", "ListarContasCorrentes",
                        {"pagina": 1, "registros_por_pagina": 200})
    if not data:
        print("  ⚠ Sem dados de contas correntes")
        return [], []

    contas_raw = data.get("ListarContasCorrentes", [])
    hoje = datetime.now()
    # Usar D-1 (ontem) como referência — hoje está naturalmente desconciliado
    ontem = hoje - timedelta(days=1)
    d_fim = ontem.strftime("%d/%m/%Y")
    mes_corrente = hoje.strftime("%Y-%m")

    # ---- 1) Saldo de cada conta (snapshot de D-1) ----
    saldos = []
    contas_ativas = []
    for cc in contas_raw:
        cc_id = cc.get("nCodCC")
        if not cc_id or cc_id in CONTAS_IGNORAR:
            continue
        nome = cc.get("descricao", "Sem nome")
        tipo = cc.get("cCodTipo", cc.get("tipo", ""))

        snap = _extrato_snapshot(cc_id, f"01/01/{ontem.year}", d_fim)
        saldo = snap["saldo"] if snap else 0
        saldo_conc = snap["saldo_conciliado"] if snap else 0
        dif = round(saldo - saldo_conc, 2)

        saldos.append({
            "id": cc_id, "nome": nome, "tipo": tipo,
            "saldo": saldo,
            "saldo_conciliado": saldo_conc,
            "diferenca": dif,
            "data": d_fim
        })
        if saldo != 0 or saldo_conc != 0:
            contas_ativas.append((cc_id, nome))
            print(f"  {nome}: Saldo={saldo:.2f} Concil={saldo_conc:.2f} Dif={dif:.2f}", flush=True)

    print(f"  ✅ {len(saldos)} contas ({len(contas_ativas)} ativas)", flush=True)

    # ---- 2) Histórico: reutilizar meses antigos do cache ----
    print("\n📥 Histórico de Conciliação...", flush=True)
    historico = []

    # Separar cache em meses antigos (reutilizar) vs atual (refazer)
    cached_mensal = {}  # chave: "cc_id|YYYY-MM-DD"
    if cache_historico:
        for h in cache_historico:
            if h.get("tipo") == "mensal":
                dt_str = h.get("data", "")
                # Meses passados não mudam — reutilizar
                if dt_str and not dt_str.startswith(mes_corrente):
                    key = f"{h['banco_id']}|{dt_str}"
                    cached_mensal[key] = h

    meses_fim = []
    dt_cursor = hoje.replace(day=1) - timedelta(days=1)
    for _ in range(6):
        meses_fim.insert(0, dt_cursor)
        dt_cursor = dt_cursor.replace(day=1) - timedelta(days=1)

    # Dias do mês corrente: a cada 5 dias para reduzir chamadas
    dias_corrente = []
    for day in range(1, hoje.day + 1, 5):
        dias_corrente.append(datetime(hoje.year, hoje.month, day))
    if dias_corrente and dias_corrente[-1].day != hoje.day:
        dias_corrente.append(datetime(hoje.year, hoje.month, hoje.day))

    # Contar chamadas necessárias (excluindo cache hits)
    calls_needed = 0
    calls_cached = 0
    for cc_id, nome in contas_ativas:
        for dt in meses_fim:
            key = f"{cc_id}|{dt.strftime('%Y-%m-%d')}"
            if key in cached_mensal:
                calls_cached += 1
            else:
                calls_needed += 1
        calls_needed += len(dias_corrente)  # Dias sempre refaz

    total_calls = calls_needed + calls_cached
    print(f"  {len(contas_ativas)} contas × ({len(meses_fim)} meses + {len(dias_corrente)} dias)", flush=True)
    print(f"  📦 {calls_cached} do cache, {calls_needed} chamadas API necessárias", flush=True)

    for cc_id, nome in contas_ativas:
        # Meses: reutilizar cache quando possível
        for dt in meses_fim:
            dt_str = dt.strftime("%Y-%m-%d")
            key = f"{cc_id}|{dt_str}"
            if key in cached_mensal:
                historico.append(cached_mensal[key])
                continue
            time.sleep(0.05)
            snap = _extrato_snapshot(cc_id, f"01/{dt.month:02d}/{dt.year}", dt.strftime("%d/%m/%Y"))
            if snap:
                historico.append({
                    "banco_id": cc_id, "banco_nome": nome,
                    "data": dt_str,
                    "label": dt.strftime("%b/%y"),
                    "saldo_atual": snap["saldo"],
                    "saldo_conciliado": snap["saldo_conciliado"],
                    "diferenca": round(snap["saldo"] - snap["saldo_conciliado"], 2),
                    "tipo": "mensal"
                })

        # Dias: sempre refazer (dados mudam)
        for dt in dias_corrente:
            time.sleep(0.05)
            snap = _extrato_snapshot(cc_id, f"01/{dt.month:02d}/{dt.year}", dt.strftime("%d/%m/%Y"))
            if snap:
                historico.append({
                    "banco_id": cc_id, "banco_nome": nome,
                    "data": dt.strftime("%Y-%m-%d"),
                    "label": dt.strftime("%d/%m"),
                    "saldo_atual": snap["saldo"],
                    "saldo_conciliado": snap["saldo_conciliado"],
                    "diferenca": round(snap["saldo"] - snap["saldo_conciliado"], 2),
                    "tipo": "diario"
                })

        print(f"  ✅ {nome}", flush=True)

    print(f"  ✅ {len(historico)} registros de histórico", flush=True)
    return saldos, historico


_clientes_raw_cache = None  # Cache dos registros raw do ListarClientes

def construir_mapa_clientes_bulk():
    """Busca TODOS os clientes via ListarClientes (paginado em bulk).
    Muito mais rápido que ConsultarCliente individual (~19 páginas vs ~1800 chamadas).
    Também guarda os registros raw para reutilização em coletar_clientes()."""
    global _clientes_raw_cache
    print("  🔍 Buscando todos clientes via ListarClientes (bulk)...")
    registros = paginar("geral/clientes", "ListarClientes",
                        {"clientesFiltro": {"codigo_cliente_omie": 0}}, "clientes_cadastro")
    _clientes_raw_cache = registros  # Guardar para reuso
    cli_map = {}
    for r in registros:
        cid = r.get("codigo_cliente_omie")
        nome = r.get("nome_fantasia") or r.get("razao_social") or ""
        if cid and nome:
            cli_map[cid] = nome
    print(f"  ✅ {len(cli_map)} clientes mapeados (bulk)")
    return cli_map


def construir_mapa_clientes_incremental(lancamentos_raw, cache_cli_map=None):
    """Busca nomes de clientes/fornecedores, reutilizando cache existente.
    Se não há cache, usa bulk (ListarClientes). Se há, busca apenas novos."""
    # Coletar todos os IDs usados nos lançamentos
    ids_todos = set()
    for r in lancamentos_raw:
        cid = r.get("codigo_cliente_fornecedor")
        if cid:
            ids_todos.add(cid)

    # Se não tem cache, buscar tudo em bulk (MUITO mais rápido)
    if not cache_cli_map:
        return construir_mapa_clientes_bulk()

    cli_map = dict(cache_cli_map)

    # Filtrar apenas IDs que NÃO estão no cache
    ids_novos = [cid for cid in ids_todos if cid not in cli_map]

    if not ids_novos:
        print(f"  ✅ Todos {len(ids_todos)} clientes já no cache — 0 buscas necessárias")
        return cli_map

    # Poucos IDs novos: buscar individualmente (mais rápido que bulk para <50)
    if len(ids_novos) <= 50:
        print(f"  📦 {len(cli_map)} do cache, buscando {len(ids_novos)} novos individualmente...")
        encontrados = 0
        for cid in ids_novos:
            try:
                data = omie_request("geral/clientes", "ConsultarCliente",
                                    {"codigo_cliente_omie": cid})
                if data:
                    nome = data.get("nome_fantasia") or data.get("razao_social") or ""
                    if nome:
                        cli_map[cid] = nome
                        encontrados += 1
                time.sleep(0.05)
            except Exception:
                pass
        print(f"  ✅ {encontrados} novos nomes (total: {len(cli_map)})")
        return cli_map

    # Muitos novos: refazer bulk
    print(f"  📦 {len(ids_novos)} novos clientes — refazendo bulk...")
    return construir_mapa_clientes_bulk()


def coletar_lancamentos(cat_map, proj_map, cache_cli_map=None):
    lancamentos = []

    # Contas a Receber
    print("\n📥 Contas a Receber...", flush=True)
    cr_raw = paginar("financas/contareceber", "ListarContasReceber", {}, "conta_receber_cadastro")

    # Contas a Pagar
    print("\n📥 Contas a Pagar...", flush=True)
    cp_raw = paginar("financas/contapagar", "ListarContasPagar", {}, "conta_pagar_cadastro")

    # Mapa de clientes/fornecedores (INCREMENTAL)
    print("\n📥 Clientes/Fornecedores...", flush=True)
    cli_map = construir_mapa_clientes_incremental(cr_raw + cp_raw, cache_cli_map)

    ignorados = 0
    for r in cr_raw:
        if r.get("id_conta_corrente") in CONTAS_IGNORAR:
            ignorados += 1
            continue
        cat_cod = r.get("codigo_categoria", "")
        proj_id = r.get("codigo_projeto")
        cli_id = r.get("codigo_cliente_fornecedor")
        lancamentos.append({
            "id": r.get("codigo_lancamento_omie"),
            "valor": float(r.get("valor_documento", 0) or 0),
            "status": (r.get("status_titulo", "") or "").upper(),
            "data": r.get("data_vencimento", ""),
            "categoria": cat_cod,
            "categoria_nome": cat_map.get(cat_cod, cat_cod),
            "projeto": proj_id,
            "projeto_nome": proj_map.get(proj_id, "Sem projeto") if proj_id else "Sem projeto",
            "cliente_nome": cli_map.get(cli_id, ""),
            "tipo": "entrada"
        })
    print(f"  ✅ {len(cr_raw) - ignorados} receber ({ignorados} ignorados)")

    ignorados = 0
    for r in cp_raw:
        if r.get("id_conta_corrente") in CONTAS_IGNORAR:
            ignorados += 1
            continue
        cat_cod = r.get("codigo_categoria", "")
        proj_id = r.get("codigo_projeto")
        cli_id = r.get("codigo_cliente_fornecedor")
        lancamentos.append({
            "id": r.get("codigo_lancamento_omie"),
            "valor": float(r.get("valor_documento", 0) or 0),
            "status": (r.get("status_titulo", "") or "").upper(),
            "data": r.get("data_vencimento", ""),
            "categoria": cat_cod,
            "categoria_nome": cat_map.get(cat_cod, cat_cod),
            "projeto": proj_id,
            "projeto_nome": proj_map.get(proj_id, "Sem projeto") if proj_id else "Sem projeto",
            "cliente_nome": cli_map.get(cli_id, ""),
            "tipo": "saida"
        })
    print(f"  ✅ {len(cp_raw) - ignorados} pagar ({ignorados} ignorados)")

    return lancamentos, cli_map


def coletar_clientes(registros_cache=None):
    """Gera resumo de clientes. Se registros_cache fornecido, reutiliza (evita paginação dupla)."""
    print("\n📥 Clientes (resumo)...")
    if registros_cache is not None:
        registros = registros_cache
        print(f"  📦 Reutilizando {len(registros)} registros do bulk anterior")
    else:
        registros = paginar("geral/clientes", "ListarClientes",
                            {"clientesFiltro": {"codigo_cliente_omie": 0}}, "clientes_cadastro")
    resumo = {
        "total_clientes": len(registros), "por_estado": {}, "por_mes_cadastro": {},
        "ativos": 0, "inativos": 0, "pessoa_fisica": 0, "pessoa_juridica": 0
    }
    for r in registros:
        estado = r.get("estado", "N/I") or "N/I"
        resumo["por_estado"][estado] = resumo["por_estado"].get(estado, 0) + 1
        if r.get("inativo", "N") == "S":
            resumo["inativos"] += 1
        else:
            resumo["ativos"] += 1
        if r.get("pessoa_fisica", "N") == "S":
            resumo["pessoa_fisica"] += 1
        else:
            resumo["pessoa_juridica"] += 1
        dt_inc = r.get("info", {}).get("dInc", "")
        if dt_inc:
            try:
                dt = datetime.strptime(dt_inc[:10], "%d/%m/%Y")
                resumo["por_mes_cadastro"][dt.strftime("%Y-%m")] = resumo["por_mes_cadastro"].get(dt.strftime("%Y-%m"), 0) + 1
            except (ValueError, TypeError):
                pass
    top_est = sorted(resumo["por_estado"].items(), key=lambda x: x[1], reverse=True)[:15]
    resumo["por_estado"] = dict(top_est)
    print(f"  ✅ {len(registros)} clientes")
    return resumo


def coletar_vendas():
    print("\n📥 Pedidos de Venda...")
    registros = paginar("produtos/pedido", "ListarPedidos",
                        {"apenas_importado_api": "N"}, "pedido_venda_produto")
    etapa_map = {"10": "Em Aberto", "20": "Em Aprovação", "30": "Aprovado",
                 "40": "Separar", "50": "Em Separação", "60": "Faturado",
                 "70": "Cancelado", "80": "Entregue"}
    resumo = {
        "total_vendas": 0, "quantidade_pedidos": len(registros), "ticket_medio": 0,
        "por_mes": {}, "por_etapa": {}, "top_produtos": {}
    }
    for r in registros:
        cab = r.get("cabecalho", {})
        tp = r.get("total_pedido", {})
        valor = float(tp.get("valor_mercadorias", 0) or 0)
        etapa = etapa_map.get(str(cab.get("etapa", "")), str(cab.get("etapa", "")))
        data = cab.get("data_previsao", "")
        resumo["total_vendas"] += valor
        resumo["por_etapa"][etapa] = resumo["por_etapa"].get(etapa, 0) + 1
        try:
            dt = datetime.strptime(data, "%d/%m/%Y")
            mes = dt.strftime("%Y-%m")
            if mes not in resumo["por_mes"]:
                resumo["por_mes"][mes] = {"valor": 0, "qtd": 0}
            resumo["por_mes"][mes]["valor"] += valor
            resumo["por_mes"][mes]["qtd"] += 1
        except (ValueError, TypeError):
            pass
        for item in r.get("det", []):
            prod = item.get("produto", {})
            nome = prod.get("descricao", "Sem nome")
            qtd = float(prod.get("quantidade", 0) or 0)
            val = float(prod.get("valor_total", 0) or 0)
            if nome not in resumo["top_produtos"]:
                resumo["top_produtos"][nome] = {"qtd": 0, "valor": 0}
            resumo["top_produtos"][nome]["qtd"] += qtd
            resumo["top_produtos"][nome]["valor"] += val
    if resumo["quantidade_pedidos"] > 0:
        resumo["ticket_medio"] = resumo["total_vendas"] / resumo["quantidade_pedidos"]
    top = sorted(resumo["top_produtos"].items(), key=lambda x: x[1]["valor"], reverse=True)[:10]
    resumo["top_produtos"] = dict(top)
    print(f"  ✅ {len(registros)} pedidos")
    return resumo


# ============================================================
# MAIN
# ============================================================

def main():
    force_full = "--full" in sys.argv
    t_start = time.time()

    print("=" * 50)
    print(f"🔄 OMIE DATA SYNC v6 {'(COMPLETO)' if force_full else '(INCREMENTAL)'}")
    print(f"   {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 50)

    if OMIE_APP_KEY == "SUA_APP_KEY_AQUI":
        print("\n❌ Configure suas credenciais!")
        sys.exit(1)

    # ---- Carregar cache ----
    cache = None if force_full else carregar_cache()
    cache_cli_map = None
    cache_cat_map = None

    if cache:
        # Extrair mapa de clientes do cache (id -> nome)
        cache_cli_map = {}
        for l in cache.get("lancamentos", []):
            cli_nome = l.get("cliente_nome", "")
            lid = l.get("id")
            # Não temos o cli_id direto nos lancamentos, mas temos no _cache
            if cli_nome:
                pass  # Será tratado abaixo

        # Usar _cache.cli_map se existir (salvo no v6+)
        raw_cli_map = cache.get("_cache", {}).get("cli_map", None)
        cache_cat_map = cache.get("_cache", {}).get("cat_map", None)

        # Fix: converter keys de string para int (API usa int, JSON salva como string)
        if raw_cli_map:
            cache_cli_map = {}
            for k, v in raw_cli_map.items():
                try:
                    cache_cli_map[int(k)] = v
                except (ValueError, TypeError):
                    cache_cli_map[k] = v
            print(f"  📦 Cache de clientes: {len(cache_cli_map)} nomes")
        else:
            print(f"  📦 Sem cache de clientes (primeiro sync incremental)")
        if cache_cat_map:
            print(f"  📦 Cache de categorias: {len(cache_cat_map)} nomes")

    # ---- Coleta ----
    cat_map = coletar_categorias(cache_cat_map)
    proj_map = coletar_projetos()
    cache_historico = cache.get("historico_conciliacao", []) if cache else None
    saldos, historico_conciliacao = coletar_saldos_bancarios(cache_historico)
    lancamentos, cli_map = coletar_lancamentos(cat_map, proj_map, cache_cli_map)
    cat_map = completar_categorias(cat_map, lancamentos)

    # Atualizar categoria_nome nos lançamentos (após completar_categorias)
    for l in lancamentos:
        cat_cod = l.get("categoria", "")
        if cat_cod and cat_cod in cat_map:
            l["categoria_nome"] = cat_map[cat_cod]

    clientes = coletar_clientes(_clientes_raw_cache)
    vendas = coletar_vendas()

    # Proteção: não salvar se a API falhou (dados vazios)
    if not lancamentos and not saldos:
        print("\n⚠ ATENÇÃO: Nenhum dado coletado (API fora do ar?)")
        print("  Arquivo anterior preservado. Tente novamente mais tarde.")
        sys.exit(1)

    # Lista de projetos com movimentação
    proj_ids = set()
    for l in lancamentos:
        if l.get("projeto"):
            proj_ids.add(l["projeto"])
    projetos_lista = sorted(
        [{"id": pid, "nome": proj_map.get(pid, str(pid))} for pid in proj_ids],
        key=lambda x: x["nome"]
    )

    # Converter cli_map keys para strings para JSON
    cli_map_str = {str(k): v for k, v in cli_map.items()} if cli_map else {}
    cat_map_str = {str(k): v for k, v in cat_map.items()} if cat_map else {}

    dados = {
        "atualizado_em": datetime.now().isoformat(),
        "atualizado_em_formatado": datetime.now().strftime("%d/%m/%Y às %H:%M"),
        "lancamentos": lancamentos,
        "categorias": cat_map,
        "projetos": projetos_lista,
        "saldos_bancarios": saldos,
        "historico_conciliacao": historico_conciliacao,
        "vendas": vendas,
        "clientes": clientes,
        "_cache": {
            "cli_map": cli_map_str,
            "cat_map": cat_map_str,
        },
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)

    # Gerar também o .js para auto-load no dashboard (abre direto do file://)
    # Exclui _cache do .js para reduzir tamanho
    dados_js = {k: v for k, v in dados.items() if k != "_cache"}
    js_file = OUTPUT_FILE.replace(".json", ".js")
    with open(js_file, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by omie_sync.py\nwindow.OMIE_DATA = ")
        json.dump(dados_js, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    elapsed = time.time() - t_start
    print(f"\n✅ Dados salvos em: {OUTPUT_FILE}")
    print(f"   + {js_file} (auto-load no dashboard)")
    print(f"   Tamanho JSON: {os.path.getsize(OUTPUT_FILE) / 1024:.1f} KB")
    print(f"   Tamanho JS:   {os.path.getsize(js_file) / 1024:.1f} KB")
    print(f"   Lançamentos: {len(lancamentos)}")
    print(f"   Projetos: {len(projetos_lista)}")
    print(f"   Categorias: {len(cat_map)}")
    print(f"   Clientes no cache: {len(cli_map_str)}")
    print(f"   ⏱ Tempo total: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print("=" * 50)


if __name__ == "__main__":
    main()
