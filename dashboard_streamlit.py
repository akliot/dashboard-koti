#!/usr/bin/env python3
"""
Dashboard Financeiro — Studio Koti
Streamlit app que lê dados do BigQuery e renderiza 7 páginas de dashboard.

Uso local:
  export GOOGLE_APPLICATION_CREDENTIALS=/path/to/gcp-key.json
  export GCP_PROJECT_ID=dashboard-koti-omie
  streamlit run dashboard_streamlit.py

Deploy (Streamlit Community Cloud):
  Configurar secrets com [gcp_service_account] contendo o JSON da service account.
"""

import os
from datetime import date, timedelta
from html import escape

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from google.cloud import bigquery
from google.oauth2 import service_account

# ============================================================
# CONFIG
# ============================================================
st.set_page_config(
    page_title="Studio Koti — Dashboard",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "dashboard-koti-omie")
BQ_DATASET = os.environ.get("BQ_DATASET", "studio_koti")

# Cores (mesmas do dashboard_omie.html)
C_BG = "#0f172a"
C_CARD = "#1e293b"
C_CARD_HOVER = "#263348"
C_BORDER = "#334155"
C_TEXT = "#e2e8f0"
C_MUTED = "#94a3b8"
C_ACCENT = "#3b82f6"
C_GREEN = "#22c55e"
C_RED = "#ef4444"
C_YELLOW = "#eab308"
C_PURPLE = "#a855f7"
C_CYAN = "#06b6d4"
C_ORANGE = "#f97316"

PALETTE = [C_ACCENT, C_GREEN, C_RED, C_YELLOW, C_PURPLE, C_CYAN, C_ORANGE, "#ec4899", "#14b8a6", "#8b5cf6"]

PAGES = [
    "Visão Geral",
    "Fluxo de Caixa",
    "Financeiro",
    "Conciliação",
    "Vendas",
    "Projetos",
    "Real vs Orçado",
]

# ============================================================
# GLOBAL CSS
# ============================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

/* Base */
html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

/* Hide Streamlit chrome */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header[data-testid="stHeader"] {display: none !important;}

/* Tighter layout */
.block-container {padding-top: 0 !important; padding-bottom: 1rem;}
section[data-testid="stSidebar"] {background: #0d1321;}
section[data-testid="stSidebar"] > div {padding-top: 1rem;}

/* Remove default metric styling */
div[data-testid="stMetric"] {display: none;}

/* Section titles */
h1, h2, h3 {color: #e2e8f0 !important; font-family: 'Inter', sans-serif !important;}
h2 {font-size: 16px !important; font-weight: 600 !important; margin-bottom: 12px !important;}
h3 {font-size: 14px !important; font-weight: 600 !important;}

/* KPI cards */
.kpi-row {display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 20px;}
.kpi-card {background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 16px; transition: transform .15s;}
.kpi-card:hover {transform: translateY(-2px);}
.kpi-label {font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: #94a3b8; margin-bottom: 6px;}
.kpi-value {font-size: 24px; font-weight: 700;}
.kpi-sub {font-size: 11px; color: #94a3b8; margin-top: 3px;}

/* Custom tables */
.custom-table {width: 100%; border-collapse: collapse; font-size: 13px; font-family: 'Inter', sans-serif;}
.custom-table thead {position: sticky; top: 0; z-index: 1;}
.custom-table th {
    text-align: left; padding: 8px 10px; font-size: 11px; text-transform: uppercase;
    letter-spacing: .5px; color: #94a3b8; border-bottom: 1px solid #334155;
    background: #1e293b; font-weight: 600;
}
.custom-table td {padding: 8px 10px; border-bottom: 1px solid #334155; color: #e2e8f0;}
.custom-table tr:hover td {background: rgba(59,130,246,.05);}
.custom-table .val-green {color: #22c55e;}
.custom-table .val-red {color: #ef4444;}
.table-wrap {max-height: 500px; overflow-y: auto; border-radius: 8px; border: 1px solid #334155;}

/* Conciliation cards */
.concil-grid {display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 10px;}
.concil-item {background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 14px; position: relative; overflow: hidden;}
.concil-item.ok {border-left: 4px solid #22c55e;}
.concil-item.warn {border-left: 4px solid #eab308;}
.concil-item.error {border-left: 4px solid #ef4444;}
.concil-header {display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;}
.concil-nome {font-size: 13px; font-weight: 600; color: #e2e8f0;}
.concil-badge {font-size: 10px; padding: 2px 8px; border-radius: 10px; font-weight: 600; text-transform: uppercase;}
.concil-badge.ok {background: rgba(34,197,94,.15); color: #22c55e;}
.concil-badge.warn {background: rgba(234,179,8,.15); color: #eab308;}
.concil-badge.error {background: rgba(239,68,68,.15); color: #ef4444;}
.concil-row {display: flex; justify-content: space-between; font-size: 12px; color: #94a3b8; margin-top: 4px;}
.concil-row .val {color: #e2e8f0; font-weight: 500;}

/* Chart cards */
.chart-card {background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 16px; margin-bottom: 16px;}
.chart-card h3 {font-size: 14px; font-weight: 600; margin-bottom: 12px; color: #e2e8f0;}

/* Sidebar radio as tabs */
div[data-testid="stSidebar"] div[role="radiogroup"] label {
    font-size: 13px !important; font-weight: 500 !important;
}
</style>
""", unsafe_allow_html=True)


# ============================================================
# BIGQUERY CONNECTION
# ============================================================

@st.cache_resource
def get_bq_client() -> bigquery.Client:
    """Retorna cliente BigQuery (cached)."""
    try:
        creds_dict = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(dict(creds_dict))
        project = creds_dict.get("project_id", GCP_PROJECT)
        return bigquery.Client(credentials=creds, project=project)
    except Exception:
        return bigquery.Client(project=GCP_PROJECT)


def _tbl(name: str) -> str:
    """Referência completa da tabela/view."""
    client = get_bq_client()
    return f"`{client.project}.{BQ_DATASET}.{name}`"


# ============================================================
# CACHED QUERIES
# ============================================================

@st.cache_data(ttl=3600, show_spinner="Carregando lançamentos...")
def load_lancamentos() -> pd.DataFrame:
    client = get_bq_client()
    q = f"SELECT * FROM {_tbl('lancamentos')}"
    df = client.query(q).to_dataframe()
    for col in ["data_vencimento", "data_emissao", "sync_date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col]).dt.date
    return df


@st.cache_data(ttl=3600, show_spinner="Carregando saldos...")
def load_saldos() -> pd.DataFrame:
    client = get_bq_client()
    return client.query(f"SELECT * FROM {_tbl('saldos_bancarios')}").to_dataframe()


@st.cache_data(ttl=3600, show_spinner="Carregando histórico...")
def load_historico() -> pd.DataFrame:
    client = get_bq_client()
    return client.query(f"SELECT * FROM {_tbl('v_historico_saldos')}").to_dataframe()


@st.cache_data(ttl=7200)
def load_projetos() -> pd.DataFrame:
    client = get_bq_client()
    return client.query(f"SELECT * FROM {_tbl('projetos')} ORDER BY nome").to_dataframe()


@st.cache_data(ttl=7200)
def load_clientes() -> pd.DataFrame:
    client = get_bq_client()
    return client.query(f"SELECT * FROM {_tbl('clientes')}").to_dataframe()


@st.cache_data(ttl=3600, show_spinner="Carregando vendas...")
def load_vendas() -> pd.DataFrame:
    client = get_bq_client()
    df = client.query(f"SELECT * FROM {_tbl('vendas_pedidos')}").to_dataframe()
    if "data_previsao" in df.columns:
        df["data_previsao"] = pd.to_datetime(df["data_previsao"]).dt.date
    return df


@st.cache_data(ttl=3600, show_spinner="Carregando orçamento...")
def load_orcamento() -> pd.DataFrame:
    client = get_bq_client()
    return client.query(f"SELECT * FROM {_tbl('orcamento_dre')}").to_dataframe()


@st.cache_data(ttl=1800)
def load_sync_log() -> pd.DataFrame:
    client = get_bq_client()
    return client.query(
        f"SELECT * FROM {_tbl('sync_log')} ORDER BY started_at DESC LIMIT 10"
    ).to_dataframe()


# ============================================================
# UI HELPERS
# ============================================================

def fmt_brl(val: float) -> str:
    """Formata valor em R$ compacto."""
    if abs(val) >= 1_000_000:
        return f"R$ {val / 1_000_000:,.1f}M".replace(",", "X").replace(".", ",").replace("X", ".")
    if abs(val) >= 1_000:
        return f"R$ {val / 1_000:,.1f}K".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_brl_full(val: float) -> str:
    """Formata valor completo R$ 1.234.567,89."""
    return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def val_color(val: float) -> str:
    """Retorna cor baseada no valor."""
    if val > 0:
        return C_GREEN
    if val < 0:
        return C_RED
    return C_TEXT


def kpi_card(label: str, value: str, color: str = C_ACCENT, subtitle: str = "") -> str:
    """Retorna HTML de um KPI card."""
    sub_html = f'<div class="kpi-sub">{escape(subtitle)}</div>' if subtitle else ""
    return f'''<div class="kpi-card">
        <div class="kpi-label">{escape(label)}</div>
        <div class="kpi-value" style="color:{color}">{value}</div>
        {sub_html}
    </div>'''


def kpi_row(cards: list[str]) -> None:
    """Renderiza uma row de KPI cards."""
    html = '<div class="kpi-row">' + "".join(cards) + '</div>'
    st.markdown(html, unsafe_allow_html=True)


def html_table(df: pd.DataFrame, max_height: int = 500) -> None:
    """Renderiza DataFrame como tabela HTML customizada."""
    if df.empty:
        st.caption("Sem dados.")
        return

    header = "".join(f"<th>{escape(str(c))}</th>" for c in df.columns)
    rows = []
    for _, row in df.iterrows():
        cells = []
        for c in df.columns:
            val = row[c]
            cell_str = str(val) if pd.notna(val) else "—"
            css_class = ""
            if isinstance(val, (int, float)):
                if val > 0 and any(kw in str(c).lower() for kw in ["resultado", "margem", "receita"]):
                    css_class = ' class="val-green"'
                elif val < 0:
                    css_class = ' class="val-red"'
            elif isinstance(val, str) and val.startswith("R$"):
                # Check if negative
                if val.replace("R$ ", "").replace(".", "").replace(",", "").strip().startswith("-"):
                    css_class = ' class="val-red"'
            cells.append(f"<td{css_class}>{escape(cell_str)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")

    html = f'''<div class="table-wrap" style="max-height:{max_height}px">
    <table class="custom-table">
        <thead><tr>{header}</tr></thead>
        <tbody>{"".join(rows)}</tbody>
    </table></div>'''
    st.markdown(html, unsafe_allow_html=True)


def dark_layout(fig: go.Figure, height: int = 400) -> go.Figure:
    """Aplica tema escuro premium a qualquer figura Plotly."""
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C_TEXT, family="Inter, sans-serif", size=12),
        xaxis=dict(
            gridcolor="rgba(51,65,85,0.5)", zerolinecolor="rgba(51,65,85,0.5)",
            tickfont=dict(size=11, color=C_MUTED),
        ),
        yaxis=dict(
            gridcolor="rgba(51,65,85,0.5)", zerolinecolor="rgba(51,65,85,0.5)",
            tickfont=dict(size=11, color=C_MUTED),
        ),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=11, color=C_MUTED)),
        margin=dict(l=20, r=20, t=40, b=20),
        height=height,
        hoverlabel=dict(bgcolor=C_CARD, font_size=12, font_family="Inter, sans-serif"),
    )
    return fig


def chart_card(title: str) -> None:
    """Abre um container visual de gráfico (usar com st.markdown antes do chart)."""
    st.markdown(f'<div class="chart-card"><h3>{escape(title)}</h3></div>', unsafe_allow_html=True)


def filter_df(df: pd.DataFrame, dt_start: date, dt_end: date, projeto: str, tipo: str) -> pd.DataFrame:
    """Aplica filtros globais ao DataFrame de lançamentos."""
    mask = pd.Series(True, index=df.index)
    if "data_vencimento" in df.columns:
        mask &= (df["data_vencimento"] >= dt_start) & (df["data_vencimento"] <= dt_end)
    if projeto != "Todos" and "projeto_nome" in df.columns:
        mask &= df["projeto_nome"] == projeto
    if tipo != "Todos" and "tipo" in df.columns:
        mask &= df["tipo"] == tipo.lower()
    return df[mask]


# ============================================================
# HEADER
# ============================================================

def render_header() -> None:
    """Renderiza header com gradiente e status do sync."""
    sync_html = ""
    try:
        df_sync = load_sync_log()
        if not df_sync.empty:
            last = df_sync.iloc[0]
            status = last.get("status", "?")
            ts = last.get("finished_at") or last.get("started_at")
            ts_str = str(ts)[:19] if ts else "—"
            if status == "success":
                dot_color = C_GREEN
                status_text = f"Sync OK — {ts_str}"
            elif status == "failed":
                dot_color = C_RED
                status_text = f"Sync falhou — {ts_str}"
            else:
                dot_color = C_YELLOW
                status_text = f"Sync em andamento..."
            sync_html = f'''<div style="font-size:13px;color:{C_MUTED};display:flex;align-items:center;gap:6px;">
                <span style="display:inline-block;width:8px;height:8px;background:{dot_color};
                border-radius:50%;animation:pulse 2s infinite;"></span>
                {escape(status_text)}
            </div>'''
    except Exception:
        sync_html = f'<div style="font-size:12px;color:{C_MUTED};">Sync status indisponível</div>'

    st.markdown(f'''
    <style>@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}</style>
    <div style="background:linear-gradient(135deg,#1e3a5f 0%,#0f172a 100%);
        border-bottom:1px solid {C_BORDER}; padding:16px 24px; margin: -1rem -1rem 20px -1rem;
        display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
        <div style="font-size:22px; font-weight:700; color:{C_TEXT};">🏠 Studio Koti</div>
        {sync_html}
    </div>''', unsafe_allow_html=True)


# ============================================================
# SIDEBAR
# ============================================================

def render_sidebar() -> tuple[date, date, str, str, str]:
    """Renderiza sidebar e retorna filtros selecionados."""
    st.sidebar.markdown(
        f'<div style="text-align:center;padding:8px 0 4px;font-size:20px;font-weight:700;color:{C_TEXT};">'
        f'🏠 Studio Koti</div>'
        f'<div style="text-align:center;font-size:11px;color:{C_MUTED};margin-bottom:12px;">'
        f'Dashboard Financeiro</div>',
        unsafe_allow_html=True,
    )

    st.sidebar.divider()

    # Navegação
    page = st.sidebar.radio("Navegação", PAGES, label_visibility="collapsed")

    st.sidebar.divider()
    st.sidebar.markdown(
        f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:.5px;'
        f'color:{C_MUTED};font-weight:600;margin-bottom:8px;">Filtros</div>',
        unsafe_allow_html=True,
    )

    # Período — atalhos
    hoje = date.today()
    inicio_mes = hoje.replace(day=1)
    inicio_tri = hoje.replace(month=((hoje.month - 1) // 3) * 3 + 1, day=1)
    inicio_ano = hoje.replace(month=1, day=1)

    atalho = st.sidebar.radio(
        "Período",
        ["Mês", "Trimestre", "YTD", "Ano", "Tudo"],
        horizontal=True,
        label_visibility="collapsed",
    )
    defaults = {
        "Mês": (inicio_mes, hoje),
        "Trimestre": (inicio_tri, hoje),
        "YTD": (inicio_ano, hoje),
        "Ano": (inicio_ano, date(hoje.year, 12, 31)),
        "Tudo": (date(2020, 1, 1), date(hoje.year, 12, 31)),
    }
    dt_default = defaults[atalho]

    col1, col2 = st.sidebar.columns(2)
    dt_start = col1.date_input("De", value=dt_default[0])
    dt_end = col2.date_input("Até", value=dt_default[1])

    # Projeto
    try:
        df_proj = load_projetos()
        proj_list = ["Todos"] + df_proj["nome"].dropna().unique().tolist()
    except Exception:
        proj_list = ["Todos"]
    projeto = st.sidebar.selectbox("Projeto", proj_list)

    # Tipo
    tipo = st.sidebar.selectbox("Tipo", ["Todos", "Entrada", "Saida"])

    return dt_start, dt_end, projeto, tipo, page


# ============================================================
# PAGE 1: VISÃO GERAL
# ============================================================

def page_visao_geral(df: pd.DataFrame, df_saldos: pd.DataFrame) -> None:
    saldo_total = df_saldos["saldo"].sum() if not df_saldos.empty else 0
    entradas = df.loc[df["tipo"] == "entrada", "valor"].sum()
    saidas = df.loc[df["tipo"] == "saida", "valor"].sum()
    resultado = entradas - saidas

    kpi_row([
        kpi_card("Saldo Total D-1", fmt_brl(saldo_total), C_ACCENT),
        kpi_card("Entradas", fmt_brl(entradas), C_GREEN),
        kpi_card("Saídas", fmt_brl(saidas), C_RED),
        kpi_card("Resultado", fmt_brl(resultado), val_color(resultado)),
    ])

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown(f'<h2 style="color:{C_TEXT}">Saldo por Conta</h2>', unsafe_allow_html=True)
        if not df_saldos.empty:
            dfs = df_saldos.sort_values("saldo", ascending=True)
            colors = [C_GREEN if v >= 0 else C_RED for v in dfs["saldo"]]
            fig = go.Figure(go.Bar(
                y=dfs["conta_nome"], x=dfs["saldo"], orientation="h",
                marker_color=colors,
                text=[fmt_brl(v) for v in dfs["saldo"]],
                textposition="auto", textfont=dict(size=11),
                hovertemplate="%{y}<br>Saldo: %{text}<extra></extra>",
            ))
            st.plotly_chart(dark_layout(fig, 380), use_container_width=True)

    with col_right:
        st.markdown(f'<h2 style="color:{C_TEXT}">Fluxo Mensal</h2>', unsafe_allow_html=True)
        if not df.empty:
            df_m = df.copy()
            df_m["mes"] = pd.to_datetime(df_m["data_vencimento"]).dt.to_period("M").astype(str)
            pivot = df_m.groupby(["mes", "tipo"])["valor"].sum().reset_index()
            ent = pivot[pivot["tipo"] == "entrada"].set_index("mes")["valor"]
            sai = pivot[pivot["tipo"] == "saida"].set_index("mes")["valor"]
            meses = sorted(set(ent.index) | set(sai.index))
            fig = go.Figure()
            fig.add_trace(go.Bar(
                name="Entradas", x=meses, y=[ent.get(m, 0) for m in meses],
                marker_color=C_GREEN,
                hovertemplate="Entradas: %{y:,.0f}<extra></extra>",
            ))
            fig.add_trace(go.Bar(
                name="Saídas", x=meses, y=[sai.get(m, 0) for m in meses],
                marker_color=C_RED,
                hovertemplate="Saídas: %{y:,.0f}<extra></extra>",
            ))
            fig.update_layout(barmode="group")
            st.plotly_chart(dark_layout(fig, 380), use_container_width=True)

    st.markdown(f'<h2 style="color:{C_TEXT}">Top Categorias</h2>', unsafe_allow_html=True)
    if not df.empty:
        top = df.groupby("categoria_nome")["valor"].sum().sort_values(ascending=False).head(15).reset_index()
        top.columns = ["Categoria", "Valor"]
        top["Valor (R$)"] = top["Valor"].apply(fmt_brl_full)
        html_table(top[["Categoria", "Valor (R$)"]])


# ============================================================
# PAGE 2: FLUXO DE CAIXA
# ============================================================

def page_fluxo_caixa(df: pd.DataFrame) -> None:
    entradas = df.loc[df["tipo"] == "entrada", "valor"].sum()
    saidas = df.loc[df["tipo"] == "saida", "valor"].sum()
    saldo = entradas - saidas

    kpi_row([
        kpi_card("Total Receitas", fmt_brl(entradas), C_GREEN),
        kpi_card("Total Despesas", fmt_brl(saidas), C_RED),
        kpi_card("Saldo Período", fmt_brl(saldo), val_color(saldo)),
    ])

    col_l, col_r = st.columns(2)

    with col_l:
        st.markdown(f'<h2 style="color:{C_TEXT}">Despesas por Grupo</h2>', unsafe_allow_html=True)
        df_desp = df[df["tipo"] == "saida"].groupby("categoria_grupo")["valor"].sum().reset_index()
        if not df_desp.empty:
            fig = px.pie(df_desp, names="categoria_grupo", values="valor", hole=0.55,
                         color_discrete_sequence=PALETTE)
            fig.update_traces(textinfo="label+percent", textfont_size=11,
                              hovertemplate="%{label}<br>%{value:,.0f}<br>%{percent}<extra></extra>")
            st.plotly_chart(dark_layout(fig, 350), use_container_width=True)

    with col_r:
        st.markdown(f'<h2 style="color:{C_TEXT}">Receitas por Grupo</h2>', unsafe_allow_html=True)
        df_rec = df[df["tipo"] == "entrada"].groupby("categoria_grupo")["valor"].sum().reset_index()
        if not df_rec.empty:
            fig = px.pie(df_rec, names="categoria_grupo", values="valor", hole=0.55,
                         color_discrete_sequence=PALETTE)
            fig.update_traces(textinfo="label+percent", textfont_size=11,
                              hovertemplate="%{label}<br>%{value:,.0f}<br>%{percent}<extra></extra>")
            st.plotly_chart(dark_layout(fig, 350), use_container_width=True)

    st.markdown(f'<h2 style="color:{C_TEXT}">Detalhamento por Categoria</h2>', unsafe_allow_html=True)
    if not df.empty:
        df_p = df.copy()
        df_p["mes"] = pd.to_datetime(df_p["data_vencimento"]).dt.to_period("M").astype(str)
        pivot = df_p.pivot_table(
            index=["categoria_grupo", "categoria_nome"],
            columns="mes", values="valor", aggfunc="sum", fill_value=0,
        )
        pivot["Total"] = pivot.sum(axis=1)
        pivot = pivot.sort_values("Total", ascending=False)
        # Format for display
        display = pivot.reset_index()
        for c in display.columns:
            if c not in ["categoria_grupo", "categoria_nome"]:
                display[c] = display[c].apply(lambda v: fmt_brl_full(v) if v != 0 else "—")
        display = display.rename(columns={"categoria_grupo": "Grupo", "categoria_nome": "Categoria"})
        html_table(display, max_height=600)


# ============================================================
# PAGE 3: FINANCEIRO
# ============================================================

def page_financeiro(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Sem dados para o período selecionado.")
        return

    df_m = df.copy()
    df_m["mes"] = pd.to_datetime(df_m["data_vencimento"]).dt.to_period("M").astype(str)

    entradas_mes = df_m[df_m["tipo"] == "entrada"].groupby("mes")["valor"].sum()
    saidas_mes = df_m[df_m["tipo"] == "saida"].groupby("mes")["valor"].sum()
    meses = sorted(set(entradas_mes.index) | set(saidas_mes.index))
    resultado = [entradas_mes.get(m, 0) - saidas_mes.get(m, 0) for m in meses]

    col_l, col_r = st.columns(2)

    with col_l:
        st.markdown(f'<h2 style="color:{C_TEXT}">Receita vs Despesa Mensal</h2>', unsafe_allow_html=True)
        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Receita", x=meses, y=[entradas_mes.get(m, 0) for m in meses],
            marker_color=C_GREEN, hovertemplate="Receita: %{y:,.0f}<extra></extra>",
        ))
        fig.add_trace(go.Bar(
            name="Despesa", x=meses, y=[saidas_mes.get(m, 0) for m in meses],
            marker_color=C_RED, hovertemplate="Despesa: %{y:,.0f}<extra></extra>",
        ))
        fig.update_layout(barmode="stack")
        st.plotly_chart(dark_layout(fig, 350), use_container_width=True)

    with col_r:
        st.markdown(f'<h2 style="color:{C_TEXT}">Resultado Mensal</h2>', unsafe_allow_html=True)
        colors = [C_GREEN if v >= 0 else C_RED for v in resultado]
        fig = go.Figure(go.Bar(
            x=meses, y=resultado, marker_color=colors,
            text=[fmt_brl(v) for v in resultado], textposition="auto",
            textfont=dict(size=11),
            hovertemplate="Resultado: %{y:,.0f}<extra></extra>",
        ))
        st.plotly_chart(dark_layout(fig, 350), use_container_width=True)

    status_pendente = ["A VENCER", "ATRASADO", "VENCE HOJE", "A_VENCER"]
    col_cr, col_cp = st.columns(2)

    with col_cr:
        st.markdown(f'<h2 style="color:{C_TEXT}">Contas a Receber</h2>', unsafe_allow_html=True)
        cr = df[(df["tipo"] == "entrada") & (df["status"].isin(status_pendente))].copy()
        cr = cr.sort_values("data_vencimento")
        if not cr.empty:
            disp = cr[["cliente_nome", "categoria_nome", "valor", "data_vencimento", "status"]].head(50).copy()
            disp["valor"] = disp["valor"].apply(fmt_brl_full)
            disp.columns = ["Cliente", "Categoria", "Valor (R$)", "Vencimento", "Status"]
            html_table(disp, max_height=400)
        else:
            st.caption("Nenhuma conta a receber pendente.")

    with col_cp:
        st.markdown(f'<h2 style="color:{C_TEXT}">Contas a Pagar</h2>', unsafe_allow_html=True)
        cp = df[(df["tipo"] == "saida") & (df["status"].isin(status_pendente))].copy()
        cp = cp.sort_values("data_vencimento")
        if not cp.empty:
            disp = cp[["cliente_nome", "categoria_nome", "valor", "data_vencimento", "status"]].head(50).copy()
            disp["valor"] = disp["valor"].apply(fmt_brl_full)
            disp.columns = ["Cliente", "Categoria", "Valor (R$)", "Vencimento", "Status"]
            html_table(disp, max_height=400)
        else:
            st.caption("Nenhuma conta a pagar pendente.")


# ============================================================
# PAGE 4: CONCILIAÇÃO BANCÁRIA
# ============================================================

def page_conciliacao(df_saldos: pd.DataFrame, df_hist: pd.DataFrame) -> None:
    if df_saldos.empty:
        st.info("Sem dados de saldos bancários.")
        return

    total_saldo = df_saldos["saldo"].sum()
    total_conc = df_saldos["saldo_conciliado"].sum()
    total_dif = df_saldos["diferenca"].sum()
    pct = (total_conc / total_saldo * 100) if total_saldo != 0 else 0
    contas_ok = int((df_saldos["diferenca"].abs() < 0.01).sum())
    total_contas = len(df_saldos)

    kpi_row([
        kpi_card("% Conciliado", f"{pct:.1f}%", C_GREEN if pct > 95 else C_YELLOW if pct > 80 else C_RED),
        kpi_card("Saldo Total", fmt_brl(total_saldo), C_ACCENT),
        kpi_card("Diferença Total", fmt_brl(total_dif), val_color(-abs(total_dif))),
        kpi_card("Contas OK", f"{contas_ok}/{total_contas}", C_GREEN if contas_ok == total_contas else C_YELLOW),
    ])

    # Cards por conta
    st.markdown(f'<h2 style="color:{C_TEXT}">Saldos por Conta</h2>', unsafe_allow_html=True)
    cards_html = '<div class="concil-grid">'
    for _, conta in df_saldos.iterrows():
        dif = conta["diferenca"]
        if abs(dif) < 0.01:
            status_cls, badge_cls, badge_text = "ok", "ok", "OK"
        elif abs(dif) < 1000:
            status_cls, badge_cls, badge_text = "warn", "warn", "Atenção"
        else:
            status_cls, badge_cls, badge_text = "error", "error", "Divergente"

        cards_html += f'''<div class="concil-item {status_cls}">
            <div class="concil-header">
                <span class="concil-nome">{escape(str(conta["conta_nome"]))}</span>
                <span class="concil-badge {badge_cls}">{badge_text}</span>
            </div>
            <div class="concil-row"><span>Saldo</span><span class="val">{fmt_brl_full(conta["saldo"])}</span></div>
            <div class="concil-row"><span>Conciliado</span><span class="val">{fmt_brl_full(conta["saldo_conciliado"])}</span></div>
            <div class="concil-row"><span>Diferença</span><span class="val" style="color:{C_GREEN if abs(dif) < 0.01 else C_YELLOW if abs(dif) < 1000 else C_RED}">{fmt_brl_full(dif)}</span></div>
        </div>'''
    cards_html += '</div>'
    st.markdown(cards_html, unsafe_allow_html=True)

    # Evolução
    st.markdown(f'<h2 style="color:{C_TEXT};margin-top:20px;">Evolução da Conciliação</h2>', unsafe_allow_html=True)
    if not df_hist.empty:
        tipo_hist = st.radio("Visualização", ["Mensal", "Diário"], horizontal=True)
        tipo_filtro = "mensal" if tipo_hist == "Mensal" else "diario"
        df_h = df_hist[df_hist["tipo"] == tipo_filtro].copy()

        if not df_h.empty:
            df_h["data_referencia"] = pd.to_datetime(df_h["data_referencia"])
            contas_disp = df_h["conta_nome"].unique().tolist()
            sel_contas = st.multiselect("Contas", contas_disp, default=contas_disp[:5])
            df_h = df_h[df_h["conta_nome"].isin(sel_contas)]

            fig = px.line(
                df_h, x="data_referencia", y="diferenca", color="conta_nome",
                color_discrete_sequence=PALETTE,
            )
            fig.add_hline(y=0, line_dash="dash", line_color=C_MUTED, opacity=0.5)
            fig.update_traces(hovertemplate="%{x}<br>Diferença: %{y:,.0f}<extra></extra>")
            st.plotly_chart(dark_layout(fig, 400), use_container_width=True)
    else:
        st.caption("Histórico indisponível.")


# ============================================================
# PAGE 5: VENDAS
# ============================================================

def page_vendas(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Sem dados de vendas.")
        return

    df_ped = df.drop_duplicates(subset=["pedido_id"])
    total = df_ped["valor_mercadorias"].sum()
    qtd = len(df_ped)
    ticket = total / qtd if qtd > 0 else 0

    kpi_row([
        kpi_card("Total Vendas", fmt_brl(total), C_GREEN),
        kpi_card("Pedidos", f"{qtd:,}".replace(",", "."), C_ACCENT),
        kpi_card("Ticket Médio", fmt_brl(ticket), C_CYAN),
    ])

    col_l, col_r = st.columns(2)

    with col_l:
        st.markdown(f'<h2 style="color:{C_TEXT}">Pedidos por Etapa</h2>', unsafe_allow_html=True)
        by_etapa = df_ped.groupby("etapa").agg(
            qtd=("pedido_id", "count"),
            valor=("valor_mercadorias", "sum"),
        ).sort_values("valor", ascending=True).reset_index()
        fig = go.Figure(go.Bar(
            y=by_etapa["etapa"], x=by_etapa["valor"], orientation="h",
            marker_color=C_ACCENT,
            text=[fmt_brl(v) for v in by_etapa["valor"]], textposition="auto",
            textfont=dict(size=11),
            hovertemplate="%{y}<br>Valor: %{x:,.0f}<br><extra></extra>",
        ))
        st.plotly_chart(dark_layout(fig, 350), use_container_width=True)

    with col_r:
        st.markdown(f'<h2 style="color:{C_TEXT}">Top Produtos</h2>', unsafe_allow_html=True)
        top_prod = df.groupby("produto_descricao").agg(
            valor_total=("produto_valor_total", "sum"),
            qtd=("produto_quantidade", "sum"),
        ).sort_values("valor_total", ascending=False).head(15).reset_index()
        disp = pd.DataFrame({
            "Produto": top_prod["produto_descricao"],
            "Valor (R$)": top_prod["valor_total"].apply(fmt_brl_full),
            "Qtd": top_prod["qtd"].apply(lambda x: f"{x:,.0f}"),
        })
        html_table(disp)


# ============================================================
# PAGE 6: PROJETOS
# ============================================================

def page_projetos(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Sem dados de lançamentos para o período.")
        return

    df_proj = df[df["projeto_nome"].notna() & (df["projeto_nome"] != "Sem projeto")].copy()
    if df_proj.empty:
        st.info("Sem lançamentos vinculados a projetos.")
        return

    resumo = df_proj.groupby("projeto_nome").apply(
        lambda g: pd.Series({
            "Receita": g.loc[g["tipo"] == "entrada", "valor"].sum(),
            "Custo": g.loc[g["tipo"] == "saida", "valor"].sum(),
        })
    ).reset_index()
    resumo["Resultado"] = resumo["Receita"] - resumo["Custo"]
    resumo["Margem"] = (resumo["Resultado"] / resumo["Receita"] * 100).where(resumo["Receita"] > 0, 0)
    resumo = resumo.sort_values("Receita", ascending=False)

    kpi_row([
        kpi_card("Projetos Ativos", str(len(resumo)), C_PURPLE),
        kpi_card("Receita Total", fmt_brl(resumo["Receita"].sum()), C_GREEN),
        kpi_card("Custo Total", fmt_brl(resumo["Custo"].sum()), C_RED),
        kpi_card("Resultado Total", fmt_brl(resumo["Resultado"].sum()), val_color(resumo["Resultado"].sum())),
    ])

    busca = st.text_input("🔍 Buscar projeto", "", label_visibility="collapsed", placeholder="Buscar projeto...")
    if busca:
        resumo = resumo[resumo["projeto_nome"].str.contains(busca, case=False, na=False)]

    st.markdown(f'<h2 style="color:{C_TEXT}">Receita vs Custo por Projeto</h2>', unsafe_allow_html=True)
    top15 = resumo.head(15).sort_values("Receita", ascending=True)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Receita", y=top15["projeto_nome"], x=top15["Receita"],
        orientation="h", marker_color=C_GREEN,
        hovertemplate="Receita: %{x:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Custo", y=top15["projeto_nome"], x=top15["Custo"],
        orientation="h", marker_color=C_RED,
        hovertemplate="Custo: %{x:,.0f}<extra></extra>",
    ))
    fig.update_layout(barmode="group")
    st.plotly_chart(dark_layout(fig, max(350, len(top15) * 30)), use_container_width=True)

    st.markdown(f'<h2 style="color:{C_TEXT}">Detalhamento</h2>', unsafe_allow_html=True)
    disp = pd.DataFrame({
        "Projeto": resumo["projeto_nome"],
        "Receita": resumo["Receita"].apply(fmt_brl_full),
        "Custo": resumo["Custo"].apply(fmt_brl_full),
        "Resultado": resumo["Resultado"].apply(fmt_brl_full),
        "Margem %": resumo["Margem"].apply(lambda x: f"{x:.1f}%"),
    })
    html_table(disp)


# ============================================================
# PAGE 7: REAL VS ORÇADO
# ============================================================

def page_orcamento(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Sem dados de orçamento.")
        return

    df_real = df[df["mes_com_real"] == True]

    def _ating(label: str) -> tuple[float, float, float]:
        d = df_real[df_real["label"] == label]
        real = d["valor_real"].sum()
        bp = d["valor_bp"].sum()
        pct = (real / bp * 100) if bp != 0 else 0
        return real, bp, pct

    rec_real, rec_bp, rec_pct = _ating("Receita Bruta")
    ebitda_real, ebitda_bp, ebitda_pct = _ating("EBITDA")
    ll_real, ll_bp, ll_pct = _ating("Lucro Líquido")

    kpi_row([
        kpi_card("Receita — % Atingimento", f"{rec_pct:.0f}%",
                 C_GREEN if rec_pct >= 100 else C_YELLOW if rec_pct >= 80 else C_RED,
                 f"Real: {fmt_brl(rec_real)} | BP: {fmt_brl(rec_bp)}"),
        kpi_card("EBITDA — % Atingimento", f"{ebitda_pct:.0f}%",
                 C_GREEN if ebitda_pct >= 100 else C_YELLOW if ebitda_pct >= 80 else C_RED,
                 f"Real: {fmt_brl(ebitda_real)} | BP: {fmt_brl(ebitda_bp)}"),
        kpi_card("Lucro Líquido — % Ating.", f"{ll_pct:.0f}%",
                 C_GREEN if ll_pct >= 100 else C_YELLOW if ll_pct >= 80 else C_RED,
                 f"Real: {fmt_brl(ll_real)} | BP: {fmt_brl(ll_bp)}"),
    ])

    col_l, col_r = st.columns(2)

    for col, label, title in [(col_l, "Receita Bruta", "Receita: Real vs BP"), (col_r, "EBITDA", "EBITDA: Real vs BP")]:
        with col:
            st.markdown(f'<h2 style="color:{C_TEXT}">{escape(title)}</h2>', unsafe_allow_html=True)
            d = df[df["label"] == label].sort_values("mes")
            if not d.empty:
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    name="Real", x=d["mes"], y=d["valor_real"], marker_color=C_GREEN,
                    hovertemplate="Real: %{y:,.0f}<extra></extra>",
                ))
                fig.add_trace(go.Bar(
                    name="BP", x=d["mes"], y=d["valor_bp"], marker_color=C_ACCENT,
                    hovertemplate="BP: %{y:,.0f}<extra></extra>",
                ))
                fig.update_layout(barmode="group")
                st.plotly_chart(dark_layout(fig, 350), use_container_width=True)

    # Tabela DRE comparativa
    st.markdown(f'<h2 style="color:{C_TEXT}">DRE Comparativo</h2>', unsafe_allow_html=True)
    show_all = st.checkbox("Mostrar todos os meses", value=False)
    df_show = df if show_all else df_real

    if not df_show.empty:
        acum = df_show.groupby(["label", "section", "level"]).agg(
            Real=("valor_real", "sum"),
            BP=("valor_bp", "sum"),
        ).reset_index()
        acum["Var"] = ((acum["Real"] - acum["BP"]) / acum["BP"].abs() * 100).where(acum["BP"].abs() > 0, None)

        dre_order = [
            "Receita Bruta", "SK", "BK", "RT", "Aditivo", "Vendas RP",
            "Impostos", "ICMS", "Crédito de ICMS", "ISS", "PIS/COFINS",
            "Receita Líquida",
            "Custos Operacionais", "Comissões Externas", "Comissões Internas", "Obras (Total)",
            "Margem de Contribuição",
            "Despesas Gerais e Adm", "Salários e Encargos", "Despesas Administrativas",
            "Despesas Comerciais", "Despesas com Imóvel", "Despesas com Veículos", "Despesas com Diretoria",
            "EBITDA",
            "Receitas/Despesas Financeiras",
            "IRPJ/CSLL",
            "Lucro Líquido",
        ]
        order_map = {label: i for i, label in enumerate(dre_order)}
        acum["_order"] = acum["label"].map(order_map).fillna(99)
        acum = acum.sort_values("_order")

        disp = pd.DataFrame({
            "Linha": acum.apply(lambda r: ("\u00A0\u00A0\u00A0\u00A0" * r["level"]) + r["label"], axis=1),
            "Real (R$)": acum["Real"].apply(fmt_brl_full),
            "BP (R$)": acum["BP"].apply(fmt_brl_full),
            "Variação %": acum["Var"].apply(lambda x: f"{x:+.1f}%" if pd.notna(x) else "—"),
        })
        html_table(disp)


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    render_header()
    dt_start, dt_end, projeto, tipo, page = render_sidebar()

    try:
        df_lanc = load_lancamentos()
        df_saldos = load_saldos()
    except Exception as e:
        st.error(f"Erro ao conectar no BigQuery: {e}")
        st.info("Verifique GOOGLE_APPLICATION_CREDENTIALS ou configure st.secrets['gcp_service_account'].")
        return

    df_filtered = filter_df(df_lanc, dt_start, dt_end, projeto, tipo)

    if page == "Visão Geral":
        page_visao_geral(df_filtered, df_saldos)
    elif page == "Fluxo de Caixa":
        page_fluxo_caixa(df_filtered)
    elif page == "Financeiro":
        page_financeiro(df_filtered)
    elif page == "Conciliação":
        try:
            df_hist = load_historico()
        except Exception:
            df_hist = pd.DataFrame()
        page_conciliacao(df_saldos, df_hist)
    elif page == "Vendas":
        try:
            df_vendas = load_vendas()
        except Exception:
            df_vendas = pd.DataFrame()
        page_vendas(df_vendas)
    elif page == "Projetos":
        page_projetos(df_filtered)
    elif page == "Real vs Orçado":
        try:
            df_orc = load_orcamento()
        except Exception:
            df_orc = pd.DataFrame()
        page_orcamento(df_orc)


def check_password() -> bool:
    """Sistema de login simples. Retorna True se autenticado ou se não há senha configurada."""
    password = None
    try:
        password = st.secrets["dashboard_password"]
    except Exception:
        password = os.environ.get("DASHBOARD_PASSWORD")

    if not password:
        return True

    if st.session_state.get("authenticated"):
        return True

    # Tela de login — estilo idêntico ao dashboard_omie.html
    st.markdown(f'''
    <style>@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}</style>
    <div style="display:flex;justify-content:center;align-items:center;min-height:80vh;">
        <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;
        padding:40px;width:360px;text-align:center;">
            <h2 style="font-size:20px;margin-bottom:6px;color:{C_TEXT};">Studio Koti</h2>
            <p style="font-size:13px;color:{C_MUTED};margin-bottom:24px;">Dashboard Financeiro</p>
        </div>
    </div>''', unsafe_allow_html=True)

    pwd_input = st.text_input("Senha", type="password", key="pwd_input", label_visibility="collapsed",
                               placeholder="Senha de acesso")
    if st.button("Entrar", type="primary", use_container_width=True):
        if pwd_input == password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    return False


if __name__ == "__main__":
    if check_password():
        main()
