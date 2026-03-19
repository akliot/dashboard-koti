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
# CSS
# ============================================================
st.markdown("""
<style>
div[data-testid="stMetric"] {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 16px;
}
div[data-testid="stMetric"] label {
    color: #94a3b8;
    font-size: 0.85rem;
}
div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
    font-size: 1.5rem;
    font-weight: 700;
}
div[data-testid="stMetricDelta"] svg { display: none; }
section[data-testid="stSidebar"] {
    background: #0d1321;
}
.block-container { padding-top: 1.5rem; }
h1, h2, h3 { color: #e2e8f0; }
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
# HELPERS
# ============================================================

def fmt_brl(val: float) -> str:
    """Formata valor em R$ 1.234,56."""
    if abs(val) >= 1_000_000:
        return f"R$ {val / 1_000_000:,.1f}M".replace(",", "X").replace(".", ",").replace("X", ".")
    if abs(val) >= 1_000:
        return f"R$ {val / 1_000:,.1f}K".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_brl_full(val: float) -> str:
    """Formata valor completo R$ 1.234.567,89."""
    return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def dark_layout(fig: go.Figure, height: int = 400) -> go.Figure:
    """Aplica tema escuro a qualquer figura Plotly."""
    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_CARD,
        font=dict(color=C_TEXT, family="sans-serif", size=12),
        xaxis=dict(gridcolor=C_BORDER, zerolinecolor=C_BORDER),
        yaxis=dict(gridcolor=C_BORDER, zerolinecolor=C_BORDER),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
        margin=dict(l=20, r=20, t=40, b=20),
        height=height,
    )
    return fig


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
# SIDEBAR
# ============================================================

def render_sidebar() -> tuple[date, date, str, str, str]:
    """Renderiza sidebar e retorna filtros selecionados."""
    st.sidebar.title("🏠 Studio Koti")

    # Sync status
    try:
        df_sync = load_sync_log()
        if not df_sync.empty:
            last = df_sync.iloc[0]
            status = last.get("status", "?")
            icon = "🟢" if status == "success" else "🔴" if status == "failed" else "🟡"
            ts = last.get("finished_at") or last.get("started_at")
            st.sidebar.caption(f"{icon} Último sync: {ts}")
    except Exception:
        st.sidebar.caption("⚠ Sync status indisponível")

    st.sidebar.divider()

    # Navegação
    page = st.sidebar.radio("Navegação", PAGES, label_visibility="collapsed")

    st.sidebar.divider()
    st.sidebar.subheader("Filtros")

    # Período — atalhos
    hoje = date.today()
    inicio_mes = hoje.replace(day=1)
    inicio_tri = hoje.replace(month=((hoje.month - 1) // 3) * 3 + 1, day=1)
    inicio_ano = hoje.replace(month=1, day=1)

    atalho = st.sidebar.radio(
        "Período",
        ["Mês", "Trimestre", "YTD", "Ano", "Tudo"],
        horizontal=True,
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
    st.header("Visão Geral")

    # KPIs
    saldo_total = df_saldos["saldo"].sum() if not df_saldos.empty else 0
    entradas = df.loc[df["tipo"] == "entrada", "valor"].sum()
    saidas = df.loc[df["tipo"] == "saida", "valor"].sum()
    resultado = entradas - saidas

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Saldo Total D-1", fmt_brl(saldo_total))
    k2.metric("Entradas", fmt_brl(entradas), delta=None)
    k3.metric("Saídas", fmt_brl(saidas), delta=None)
    k4.metric("Resultado", fmt_brl(resultado), delta=f"{resultado:+,.0f}".replace(",", "."))

    col_left, col_right = st.columns(2)

    # Saldo por conta
    with col_left:
        st.subheader("Saldo por Conta")
        if not df_saldos.empty:
            dfs = df_saldos.sort_values("saldo", ascending=True)
            colors = [C_GREEN if v >= 0 else C_RED for v in dfs["saldo"]]
            fig = go.Figure(go.Bar(
                y=dfs["conta_nome"], x=dfs["saldo"], orientation="h",
                marker_color=colors, text=[fmt_brl(v) for v in dfs["saldo"]],
                textposition="auto",
            ))
            st.plotly_chart(dark_layout(fig, 350), use_container_width=True)

    # Fluxo mensal
    with col_right:
        st.subheader("Fluxo Mensal")
        if not df.empty:
            df_m = df.copy()
            df_m["mes"] = pd.to_datetime(df_m["data_vencimento"]).dt.to_period("M").astype(str)
            pivot = df_m.groupby(["mes", "tipo"])["valor"].sum().reset_index()
            ent = pivot[pivot["tipo"] == "entrada"].set_index("mes")["valor"]
            sai = pivot[pivot["tipo"] == "saida"].set_index("mes")["valor"]
            meses = sorted(set(ent.index) | set(sai.index))
            fig = go.Figure()
            fig.add_trace(go.Bar(name="Entradas", x=meses, y=[ent.get(m, 0) for m in meses], marker_color=C_GREEN))
            fig.add_trace(go.Bar(name="Saídas", x=meses, y=[sai.get(m, 0) for m in meses], marker_color=C_RED))
            fig.update_layout(barmode="group")
            st.plotly_chart(dark_layout(fig, 350), use_container_width=True)

    # Top categorias
    st.subheader("Top Categorias")
    if not df.empty:
        top = df.groupby("categoria_nome")["valor"].sum().sort_values(ascending=False).head(15).reset_index()
        top.columns = ["Categoria", "Valor"]
        top["Valor (R$)"] = top["Valor"].apply(fmt_brl_full)
        st.dataframe(top[["Categoria", "Valor (R$)"]], use_container_width=True, hide_index=True)


# ============================================================
# PAGE 2: FLUXO DE CAIXA
# ============================================================

def page_fluxo_caixa(df: pd.DataFrame) -> None:
    st.header("Fluxo de Caixa")

    # KPIs
    entradas = df.loc[df["tipo"] == "entrada", "valor"].sum()
    saidas = df.loc[df["tipo"] == "saida", "valor"].sum()
    saldo = entradas - saidas
    k1, k2, k3 = st.columns(3)
    k1.metric("Total Receitas", fmt_brl(entradas))
    k2.metric("Total Despesas", fmt_brl(saidas))
    k3.metric("Saldo Período", fmt_brl(saldo))

    col_l, col_r = st.columns(2)

    # Despesas por grupo
    with col_l:
        st.subheader("Despesas por Grupo")
        df_desp = df[df["tipo"] == "saida"].groupby("categoria_grupo")["valor"].sum().reset_index()
        if not df_desp.empty:
            fig = px.pie(df_desp, names="categoria_grupo", values="valor", hole=0.5,
                         color_discrete_sequence=PALETTE)
            st.plotly_chart(dark_layout(fig, 350), use_container_width=True)

    # Receitas por grupo
    with col_r:
        st.subheader("Receitas por Grupo")
        df_rec = df[df["tipo"] == "entrada"].groupby("categoria_grupo")["valor"].sum().reset_index()
        if not df_rec.empty:
            fig = px.pie(df_rec, names="categoria_grupo", values="valor", hole=0.5,
                         color_discrete_sequence=PALETTE)
            st.plotly_chart(dark_layout(fig, 350), use_container_width=True)

    # Pivot table
    st.subheader("Detalhamento por Categoria")
    if not df.empty:
        df_p = df.copy()
        df_p["mes"] = pd.to_datetime(df_p["data_vencimento"]).dt.to_period("M").astype(str)
        pivot = df_p.pivot_table(
            index=["categoria_grupo", "categoria_nome"],
            columns="mes", values="valor", aggfunc="sum", fill_value=0,
        )
        pivot["Total"] = pivot.sum(axis=1)
        pivot = pivot.sort_values("Total", ascending=False)
        st.dataframe(pivot.style.format("R$ {:,.0f}"), use_container_width=True)


# ============================================================
# PAGE 3: FINANCEIRO
# ============================================================

def page_financeiro(df: pd.DataFrame) -> None:
    st.header("Financeiro")

    if df.empty:
        st.info("Sem dados para o período selecionado.")
        return

    df_m = df.copy()
    df_m["mes"] = pd.to_datetime(df_m["data_vencimento"]).dt.to_period("M").astype(str)

    # Resultado mensal
    entradas_mes = df_m[df_m["tipo"] == "entrada"].groupby("mes")["valor"].sum()
    saidas_mes = df_m[df_m["tipo"] == "saida"].groupby("mes")["valor"].sum()
    meses = sorted(set(entradas_mes.index) | set(saidas_mes.index))
    resultado = [entradas_mes.get(m, 0) - saidas_mes.get(m, 0) for m in meses]

    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("Receita vs Despesa Mensal")
        fig = go.Figure()
        fig.add_trace(go.Bar(name="Receita", x=meses, y=[entradas_mes.get(m, 0) for m in meses], marker_color=C_GREEN))
        fig.add_trace(go.Bar(name="Despesa", x=meses, y=[saidas_mes.get(m, 0) for m in meses], marker_color=C_RED))
        fig.update_layout(barmode="stack")
        st.plotly_chart(dark_layout(fig, 350), use_container_width=True)

    with col_r:
        st.subheader("Resultado Mensal")
        colors = [C_GREEN if v >= 0 else C_RED for v in resultado]
        fig = go.Figure(go.Bar(x=meses, y=resultado, marker_color=colors,
                               text=[fmt_brl(v) for v in resultado], textposition="auto"))
        st.plotly_chart(dark_layout(fig, 350), use_container_width=True)

    # Contas a receber / pagar
    status_pendente = ["A VENCER", "ATRASADO", "VENCE HOJE", "A_VENCER"]
    col_cr, col_cp = st.columns(2)

    with col_cr:
        st.subheader("Contas a Receber")
        cr = df[(df["tipo"] == "entrada") & (df["status"].isin(status_pendente))].copy()
        cr = cr.sort_values("data_vencimento")
        if not cr.empty:
            cr["Valor (R$)"] = cr["valor"].apply(fmt_brl_full)
            st.dataframe(
                cr[["cliente_nome", "categoria_nome", "Valor (R$)", "data_vencimento", "status"]].head(50),
                use_container_width=True, hide_index=True,
            )
        else:
            st.caption("Nenhuma conta a receber pendente.")

    with col_cp:
        st.subheader("Contas a Pagar")
        cp = df[(df["tipo"] == "saida") & (df["status"].isin(status_pendente))].copy()
        cp = cp.sort_values("data_vencimento")
        if not cp.empty:
            cp["Valor (R$)"] = cp["valor"].apply(fmt_brl_full)
            st.dataframe(
                cp[["cliente_nome", "categoria_nome", "Valor (R$)", "data_vencimento", "status"]].head(50),
                use_container_width=True, hide_index=True,
            )
        else:
            st.caption("Nenhuma conta a pagar pendente.")


# ============================================================
# PAGE 4: CONCILIAÇÃO BANCÁRIA
# ============================================================

def page_conciliacao(df_saldos: pd.DataFrame, df_hist: pd.DataFrame) -> None:
    st.header("Conciliação Bancária")

    if df_saldos.empty:
        st.info("Sem dados de saldos bancários.")
        return

    # KPIs
    total_saldo = df_saldos["saldo"].sum()
    total_conc = df_saldos["saldo_conciliado"].sum()
    total_dif = df_saldos["diferenca"].sum()
    pct = (total_conc / total_saldo * 100) if total_saldo != 0 else 0
    contas_ok = (df_saldos["diferenca"].abs() < 0.01).sum()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("% Conciliado", f"{pct:.1f}%")
    k2.metric("Saldo Total", fmt_brl(total_saldo))
    k3.metric("Diferença Total", fmt_brl(total_dif))
    k4.metric("Contas OK", f"{contas_ok}/{len(df_saldos)}")

    # Cards por conta
    st.subheader("Saldos por Conta")
    cols_per_row = 3
    rows_data = [df_saldos.iloc[i:i + cols_per_row] for i in range(0, len(df_saldos), cols_per_row)]
    for row_data in rows_data:
        cols = st.columns(cols_per_row)
        for idx, (_, conta) in enumerate(row_data.iterrows()):
            with cols[idx]:
                dif = conta["diferenca"]
                border_color = C_GREEN if abs(dif) < 0.01 else C_YELLOW if abs(dif) < 1000 else C_RED
                st.markdown(
                    f"""<div style="background:{C_CARD}; border-left: 4px solid {border_color};
                    border-radius: 8px; padding: 12px; margin-bottom: 8px;">
                    <div style="color:{C_TEXT}; font-weight:600; font-size:0.95rem;">{conta['conta_nome']}</div>
                    <div style="color:{C_MUTED}; font-size:0.8rem; margin-top:8px;">
                        Saldo: {fmt_brl_full(conta['saldo'])}<br>
                        Conciliado: {fmt_brl_full(conta['saldo_conciliado'])}<br>
                        <span style="color:{border_color};">Diferença: {fmt_brl_full(dif)}</span>
                    </div></div>""",
                    unsafe_allow_html=True,
                )

    # Evolução da conciliação
    st.subheader("Evolução da Conciliação")
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
            fig.add_hline(y=0, line_dash="dash", line_color=C_MUTED)
            st.plotly_chart(dark_layout(fig, 400), use_container_width=True)
    else:
        st.caption("Histórico indisponível.")


# ============================================================
# PAGE 5: VENDAS
# ============================================================

def page_vendas(df: pd.DataFrame) -> None:
    st.header("Vendas")

    if df.empty:
        st.info("Sem dados de vendas.")
        return

    # Deduplicar por pedido para KPIs de pedido
    df_ped = df.drop_duplicates(subset=["pedido_id"])

    total = df_ped["valor_mercadorias"].sum()
    qtd = len(df_ped)
    ticket = total / qtd if qtd > 0 else 0

    k1, k2, k3 = st.columns(3)
    k1.metric("Total Vendas", fmt_brl(total))
    k2.metric("Pedidos", f"{qtd:,}".replace(",", "."))
    k3.metric("Ticket Médio", fmt_brl(ticket))

    col_l, col_r = st.columns(2)

    # Vendas por etapa
    with col_l:
        st.subheader("Pedidos por Etapa")
        by_etapa = df_ped.groupby("etapa").agg(
            qtd=("pedido_id", "count"),
            valor=("valor_mercadorias", "sum"),
        ).sort_values("valor", ascending=True).reset_index()
        fig = go.Figure(go.Bar(
            y=by_etapa["etapa"], x=by_etapa["valor"], orientation="h",
            marker_color=C_ACCENT,
            text=[fmt_brl(v) for v in by_etapa["valor"]], textposition="auto",
        ))
        st.plotly_chart(dark_layout(fig, 350), use_container_width=True)

    # Top produtos
    with col_r:
        st.subheader("Top Produtos")
        top_prod = df.groupby("produto_descricao").agg(
            valor_total=("produto_valor_total", "sum"),
            qtd=("produto_quantidade", "sum"),
        ).sort_values("valor_total", ascending=False).head(15).reset_index()
        top_prod.columns = ["Produto", "Valor Total", "Qtd"]
        top_prod["Valor (R$)"] = top_prod["Valor Total"].apply(fmt_brl_full)
        top_prod["Qtd"] = top_prod["Qtd"].apply(lambda x: f"{x:,.0f}")
        st.dataframe(top_prod[["Produto", "Valor (R$)", "Qtd"]], use_container_width=True, hide_index=True)


# ============================================================
# PAGE 6: PROJETOS
# ============================================================

def page_projetos(df: pd.DataFrame) -> None:
    st.header("Projetos")

    if df.empty:
        st.info("Sem dados de lançamentos para o período.")
        return

    # Filtrar apenas lançamentos com projeto
    df_proj = df[df["projeto_nome"].notna() & (df["projeto_nome"] != "Sem projeto")].copy()

    if df_proj.empty:
        st.info("Sem lançamentos vinculados a projetos.")
        return

    # Tabela por projeto
    resumo = df_proj.groupby("projeto_nome").apply(
        lambda g: pd.Series({
            "Receita": g.loc[g["tipo"] == "entrada", "valor"].sum(),
            "Custo": g.loc[g["tipo"] == "saida", "valor"].sum(),
        })
    ).reset_index()
    resumo["Resultado"] = resumo["Receita"] - resumo["Custo"]
    resumo["Margem %"] = (resumo["Resultado"] / resumo["Receita"] * 100).where(resumo["Receita"] > 0, 0)
    resumo = resumo.sort_values("Receita", ascending=False)

    # KPIs
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Projetos Ativos", len(resumo))
    k2.metric("Receita Total", fmt_brl(resumo["Receita"].sum()))
    k3.metric("Custo Total", fmt_brl(resumo["Custo"].sum()))
    k4.metric("Resultado Total", fmt_brl(resumo["Resultado"].sum()))

    # Busca
    busca = st.text_input("🔍 Buscar projeto", "")
    if busca:
        resumo = resumo[resumo["projeto_nome"].str.contains(busca, case=False, na=False)]

    # Gráfico top 15
    st.subheader("Receita vs Custo por Projeto")
    top15 = resumo.head(15).sort_values("Receita", ascending=True)
    fig = go.Figure()
    fig.add_trace(go.Bar(name="Receita", y=top15["projeto_nome"], x=top15["Receita"],
                         orientation="h", marker_color=C_GREEN))
    fig.add_trace(go.Bar(name="Custo", y=top15["projeto_nome"], x=top15["Custo"],
                         orientation="h", marker_color=C_RED))
    fig.update_layout(barmode="group")
    st.plotly_chart(dark_layout(fig, max(350, len(top15) * 30)), use_container_width=True)

    # Tabela detalhada
    st.subheader("Detalhamento")
    display = resumo.copy()
    display["Receita"] = display["Receita"].apply(fmt_brl_full)
    display["Custo"] = display["Custo"].apply(fmt_brl_full)
    display["Resultado"] = display["Resultado"].apply(fmt_brl_full)
    display["Margem %"] = display["Margem %"].apply(lambda x: f"{x:.1f}%")
    display.columns = ["Projeto", "Receita", "Custo", "Resultado", "Margem %"]
    st.dataframe(display, use_container_width=True, hide_index=True)


# ============================================================
# PAGE 7: REAL VS ORÇADO
# ============================================================

def page_orcamento(df: pd.DataFrame) -> None:
    st.header("Real vs Orçado (BP)")

    if df.empty:
        st.info("Sem dados de orçamento.")
        return

    # KPIs — apenas meses com dados reais
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

    k1, k2, k3 = st.columns(3)
    k1.metric("Receita — % Atingimento", f"{rec_pct:.0f}%", delta=f"Real: {fmt_brl(rec_real)}")
    k2.metric("EBITDA — % Atingimento", f"{ebitda_pct:.0f}%", delta=f"Real: {fmt_brl(ebitda_real)}")
    k3.metric("Lucro Líquido — % Ating.", f"{ll_pct:.0f}%", delta=f"Real: {fmt_brl(ll_real)}")

    # Gráficos: Receita e EBITDA por mês
    col_l, col_r = st.columns(2)

    for col, label, title in [(col_l, "Receita Bruta", "Receita: Real vs BP"), (col_r, "EBITDA", "EBITDA: Real vs BP")]:
        with col:
            st.subheader(title)
            d = df[df["label"] == label].sort_values("mes")
            if not d.empty:
                fig = go.Figure()
                fig.add_trace(go.Bar(name="Real", x=d["mes"], y=d["valor_real"], marker_color=C_GREEN))
                fig.add_trace(go.Bar(name="BP", x=d["mes"], y=d["valor_bp"], marker_color=C_ACCENT))
                fig.update_layout(barmode="group")
                st.plotly_chart(dark_layout(fig, 350), use_container_width=True)

    # Tabela DRE comparativa
    st.subheader("DRE Comparativo")
    show_all = st.checkbox("Mostrar todos os meses", value=False)
    df_show = df if show_all else df_real

    if not df_show.empty:
        # Pivot: linhas = label, colunas = mes (real | bp | var%)
        # Simplificado: mostrar acumulado
        acum = df_show.groupby(["label", "section", "level"]).agg(
            Real=("valor_real", "sum"),
            BP=("valor_bp", "sum"),
        ).reset_index()
        acum["Var %"] = ((acum["Real"] - acum["BP"]) / acum["BP"].abs() * 100).where(acum["BP"].abs() > 0, None)
        acum = acum.sort_values(["section", "level"])

        # Ordem do DRE
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

        # Formatação
        display = acum[["label", "level", "Real", "BP", "Var %"]].copy()
        # Indentar por level
        display["Linha"] = display.apply(
            lambda r: ("    " * r["level"]) + r["label"], axis=1
        )
        display["Real (R$)"] = display["Real"].apply(fmt_brl_full)
        display["BP (R$)"] = display["BP"].apply(fmt_brl_full)
        display["Variação %"] = display["Var %"].apply(lambda x: f"{x:+.1f}%" if pd.notna(x) else "—")

        st.dataframe(
            display[["Linha", "Real (R$)", "BP (R$)", "Variação %"]],
            use_container_width=True, hide_index=True,
        )


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    dt_start, dt_end, projeto, tipo, page = render_sidebar()

    # Carregar dados (cacheados)
    try:
        df_lanc = load_lancamentos()
        df_saldos = load_saldos()
    except Exception as e:
        st.error(f"Erro ao conectar no BigQuery: {e}")
        st.info("Verifique GOOGLE_APPLICATION_CREDENTIALS ou configure st.secrets['gcp_service_account'].")
        return

    # Aplicar filtros globais
    df_filtered = filter_df(df_lanc, dt_start, dt_end, projeto, tipo)

    # Router
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
    # Obter senha configurada
    password = None
    try:
        password = st.secrets["dashboard_password"]
    except Exception:
        password = os.environ.get("DASHBOARD_PASSWORD")

    # Sem senha configurada → modo dev, pular autenticação
    if not password:
        return True

    # Já logado
    if st.session_state.get("authenticated"):
        return True

    # Tela de login
    st.markdown(
        f"""<div style="display:flex; justify-content:center; align-items:center; min-height:60vh;">
        <div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:12px;
        padding:40px; max-width:400px; width:100%; text-align:center;">
        <h2 style="color:{C_TEXT}; margin-bottom:8px;">🏠 Studio Koti</h2>
        <p style="color:{C_MUTED};">Dashboard Financeiro</p>
        </div></div>""",
        unsafe_allow_html=True,
    )
    pwd_input = st.text_input("Senha", type="password", key="pwd_input")
    if st.button("Entrar", type="primary"):
        if pwd_input == password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    return False


if __name__ == "__main__":
    if check_password():
        main()
