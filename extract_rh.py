#!/usr/bin/env python3
"""
Extrai dados AGREGADOS da folha de pagamentos para o dashboard.
Gera rh_data.json com totais por área/rubrica — SEM dados pessoais (LGPD).

Uso:
  python3 extract_rh.py [caminho_planilha]
  python3 extract_rh.py  # busca em ~/Downloads/Folha de Pagamentos 2026.xlsx

NUNCA expor: nomes, CPF, salário individual, dados bancários.
"""

import json
import os
import sys
from datetime import date
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.expanduser("~/Downloads/Folha de Pagamentos 2026.xlsx")

# Meses e colunas no RESUMO (Col 2=Jan, 3=Fev, ..., 13=Dez)
MESES_COLS = {f"2026-{m:02d}": m + 1 for m in range(1, 13)}
MESES_NOMES = ["Janeiro 26", "Fevereiro 26", "Março 26", "Abril 26",
               "Maio 26", "Junho 26", "Julho 26", "Agosto 26",
               "Setembro 26", "Outubro 26", "Novembro 26", "Dezembro 26"]

# Rubricas no RESUMO (linhas 4-12)
RUBRICAS = [
    (4, "salarios", "Salário Base"),
    (5, "softwares", "Softwares"),
    (6, "beneficios", "Benefícios"),
    (7, "comissao", "Comissão"),
    (8, "rescisao", "Rescisão"),
    (9, "bonus", "Bônus"),
    (10, "impostos", "Impostos/Encargos"),
    (11, "treinamentos", "Treinamentos"),
    (12, "decimo_terceiro", "13º Salário"),
]

# HC no RESUMO (linhas 15-20)
HC_ROWS = {
    "inicio": 15,
    "saida_vol": 16,
    "saida_inv": 17,
    "admissoes": 18,
    "final": 19,
    "turnover": 20,
}

# Áreas no RESUMO (linhas 23-31)
AREAS_START = 23
AREAS_END = 31


def read_val(ws, row, col):
    v = ws.cell(row=row, column=col).value
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return round(v, 2)
    return 0


def detect_status(wb):
    """Detecta status de cada mês: realizado, andamento, projecao."""
    hoje = date.today()
    mes_atual = f"2026-{hoje.month:02d}"
    status = {}
    for mes_key in MESES_COLS:
        m = int(mes_key.split("-")[1])
        if mes_key < mes_atual:
            status[mes_key] = "realizado"
        elif mes_key == mes_atual:
            status[mes_key] = "andamento"
        else:
            status[mes_key] = "projecao"
    return status, mes_atual


def extract_resumo(wb):
    """Extrai dados da aba RESUMO (agregados — sem dados pessoais)."""
    ws = wb["RESUMO"]

    # Custo mensal por rubrica
    custo_mensal = {}
    for mes_key, col in MESES_COLS.items():
        mes_data = {}
        total = 0
        for row, key, _ in RUBRICAS:
            v = read_val(ws, row, col)
            mes_data[key] = v
            total += v
        mes_data["total"] = round(read_val(ws, 13, col), 2)
        custo_mensal[mes_key] = mes_data

    # HC
    hc = {}
    for mes_key, col in MESES_COLS.items():
        hc[mes_key] = {
            "inicio": int(read_val(ws, HC_ROWS["inicio"], col)),
            "admissoes": int(read_val(ws, HC_ROWS["admissoes"], col)),
            "saida_vol": int(read_val(ws, HC_ROWS["saida_vol"], col)),
            "saida_inv": int(read_val(ws, HC_ROWS["saida_inv"], col)),
            "final": int(read_val(ws, HC_ROWS["final"], col)),
            "turnover": round(read_val(ws, HC_ROWS["turnover"], col), 4),
        }

    # Custo por área
    custo_por_area = {}
    areas = []
    for row in range(AREAS_START, AREAS_END + 1):
        area = ws.cell(row=row, column=1).value
        if area and area != "CUSTO TOTAL":
            areas.append((row, area))

    for mes_key, col in MESES_COLS.items():
        area_data = {}
        for row, area in areas:
            area_data[area] = round(read_val(ws, row, col), 2)
        custo_por_area[mes_key] = area_data

    return custo_mensal, hc, custo_por_area, [a[1] for a in areas]


def extract_demografico(wb, areas_list):
    """Extrai dados demográficos AGREGADOS por área (sem dados individuais)."""
    demografico = {}
    hc_por_area = {}
    per_capita = {}

    for i, aba_nome in enumerate(MESES_NOMES):
        mes_key = f"2026-{i+1:02d}"
        if aba_nome not in wb.sheetnames:
            continue

        ws = wb[aba_nome]
        # Coletar por departamento: contagem, soma idade, soma tempo casa, soma custo
        dept_stats = defaultdict(lambda: {"count": 0, "idade_sum": 0, "tc_sum": 0, "custo_sum": 0})

        for row in range(4, ws.max_row + 1):
            dept = ws.cell(row=row, column=3).value
            if not dept:
                continue
            idade = ws.cell(row=row, column=7).value
            tc = ws.cell(row=row, column=8).value
            custo = ws.cell(row=row, column=24).value

            if not isinstance(idade, (int, float)):
                continue

            dept_stats[dept]["count"] += 1
            dept_stats[dept]["idade_sum"] += float(idade) if idade else 0
            dept_stats[dept]["tc_sum"] += float(tc) if tc else 0
            dept_stats[dept]["custo_sum"] += float(custo) if custo else 0

        # Agregar
        total_count = sum(d["count"] for d in dept_stats.values())
        total_idade = sum(d["idade_sum"] for d in dept_stats.values())
        total_tc = sum(d["tc_sum"] for d in dept_stats.values())

        demografico[mes_key] = {
            "idade_media": round(total_idade / total_count, 1) if total_count else 0,
            "tempo_casa_medio": round(total_tc / total_count, 1) if total_count else 0,
            "idade_por_area": {d: round(s["idade_sum"] / s["count"], 1) if s["count"] else 0
                               for d, s in dept_stats.items()},
            "tempo_casa_por_area": {d: round(s["tc_sum"] / s["count"], 1) if s["count"] else 0
                                    for d, s in dept_stats.items()},
        }

        hc_por_area[mes_key] = {d: s["count"] for d, s in dept_stats.items()}
        per_capita[mes_key] = {d: round(s["custo_sum"] / s["count"], 2) if s["count"] else 0
                               for d, s in dept_stats.items()}

    return demografico, hc_por_area, per_capita


def main():
    filepath = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH

    if not os.path.exists(filepath):
        print(f"⚠ Planilha não encontrada: {filepath}")
        sys.exit(0)

    from openpyxl import load_workbook
    print(f"📊 Lendo planilha: {filepath}")
    wb = load_workbook(filepath, data_only=True)

    status_meses, mes_ref = detect_status(wb)
    custo_mensal, hc, custo_por_area, areas_list = extract_resumo(wb)
    demografico, hc_por_area, per_capita = extract_demografico(wb, areas_list)

    # Composição de custo YTD (meses realizados + andamento)
    composicao_ytd = defaultdict(float)
    for mes_key, status in status_meses.items():
        if status in ("realizado", "andamento"):
            for row, key, label in RUBRICAS:
                composicao_ytd[label] += custo_mensal.get(mes_key, {}).get(key, 0)
    composicao_ytd = {k: round(v, 2) for k, v in composicao_ytd.items() if v > 0}

    result = {
        "mes_referencia": mes_ref,
        "status_meses": status_meses,
        "custo_mensal": custo_mensal,
        "hc": hc,
        "custo_por_area": custo_por_area,
        "hc_por_area": hc_por_area,
        "per_capita_area": per_capita,
        "demografico": demografico,
        "composicao_custo_ytd": composicao_ytd,
    }

    output = os.path.join(SCRIPT_DIR, "rh_data.json")
    with open(output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"  ✅ {len(custo_mensal)} meses extraídos")
    print(f"  ✅ {len(areas_list)} áreas: {', '.join(areas_list)}")
    print(f"  ✅ Salvo em: {output} ({os.path.getsize(output) / 1024:.1f} KB)")
    print(f"  🔒 Sem dados pessoais (LGPD)")


if __name__ == "__main__":
    main()
