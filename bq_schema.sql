-- ============================================================
-- BigQuery Schema — Studio Koti Dashboard
-- Execute este arquivo no BigQuery Console para criar todas as tabelas.
-- Substitua `studio_koti` pelo nome do dataset se necessário.
-- ============================================================

-- 1. Lançamentos financeiros (contas a pagar e receber)
CREATE TABLE IF NOT EXISTS `studio_koti.lancamentos` (
  id                    INT64       NOT NULL OPTIONS(description="ID do lançamento no Omie"),
  tipo                  STRING      NOT NULL OPTIONS(description="'entrada' (receber) ou 'saida' (pagar)"),
  valor                 FLOAT64     NOT NULL OPTIONS(description="Valor do documento em R$"),
  status                STRING      OPTIONS(description="Status: LIQUIDADO, RECEBIDO, ATRASADO, A_VENCER, etc."),
  data_vencimento       DATE        OPTIONS(description="Data de vencimento do título"),
  data_emissao          DATE        OPTIONS(description="Data de emissão do documento (competência)"),
  data_pagamento        DATE        OPTIONS(description="Data real de pagamento/recebimento (info.dAlt). NULL se não pago"),
  numero_documento      STRING      OPTIONS(description="Número do documento/NF"),
  categoria_codigo      STRING      OPTIONS(description="Código da categoria contábil (ex: 1.01.02)"),
  categoria_nome        STRING      OPTIONS(description="Nome da categoria (ex: Marcenaria)"),
  categoria_grupo       STRING      OPTIONS(description="Grupo da categoria — 2 primeiros níveis (ex: 1.01)"),
  projeto_id            INT64       OPTIONS(description="ID do projeto/obra no Omie"),
  projeto_nome          STRING      OPTIONS(description="Nome do projeto/obra"),
  cliente_id            INT64       OPTIONS(description="ID do cliente/fornecedor no Omie"),
  cliente_nome          STRING      OPTIONS(description="Nome fantasia ou razão social"),
  conta_corrente_id     INT64       OPTIONS(description="ID da conta corrente no Omie"),
  is_faturamento_direto BOOL        OPTIONS(description="True se FD (faturamento direto). Lógica específica do Koti"),
  sync_timestamp        TIMESTAMP   NOT NULL OPTIONS(description="Timestamp do sync que inseriu este registro"),
  sync_date             DATE        NOT NULL OPTIONS(description="Data do sync (para particionamento)")
)
PARTITION BY sync_date
CLUSTER BY tipo, categoria_grupo
OPTIONS(
  description="Lançamentos financeiros (contas a pagar e receber) do Omie ERP",
  labels=[("source", "omie"), ("domain", "financeiro")]
);

-- 2. Saldos bancários (snapshot D-1)
CREATE TABLE IF NOT EXISTS `studio_koti.saldos_bancarios` (
  conta_id              INT64       NOT NULL OPTIONS(description="ID da conta corrente no Omie"),
  conta_nome            STRING      NOT NULL OPTIONS(description="Nome/descrição da conta"),
  conta_tipo            STRING      OPTIONS(description="Tipo da conta no Omie"),
  saldo                 FLOAT64     NOT NULL OPTIONS(description="nSaldoAtual (coluna Saldo no Omie)"),
  saldo_conciliado      FLOAT64     NOT NULL OPTIONS(description="nSaldoConciliado"),
  diferenca             FLOAT64     NOT NULL OPTIONS(description="saldo - saldo_conciliado"),
  data_referencia       DATE        NOT NULL OPTIONS(description="Data de referência (D-1)"),
  sync_timestamp        TIMESTAMP   NOT NULL,
  sync_date             DATE        NOT NULL
)
PARTITION BY sync_date
OPTIONS(
  description="Saldos bancários snapshot D-1 de cada conta corrente"
);

-- 3. Histórico de saldos (evolução mensal e diária)
CREATE TABLE IF NOT EXISTS `studio_koti.historico_saldos` (
  conta_id              INT64       NOT NULL,
  conta_nome            STRING      NOT NULL,
  data_referencia       DATE        NOT NULL OPTIONS(description="Data do snapshot"),
  label                 STRING      OPTIONS(description="Label formatado (ex: Jan/26 ou 15/01)"),
  saldo_atual           FLOAT64     NOT NULL,
  saldo_conciliado      FLOAT64     NOT NULL,
  diferenca             FLOAT64     NOT NULL,
  tipo                  STRING      NOT NULL OPTIONS(description="'mensal' ou 'diario'"),
  sync_timestamp        TIMESTAMP   NOT NULL,
  sync_date             DATE        NOT NULL
)
PARTITION BY sync_date
CLUSTER BY conta_id, tipo
OPTIONS(
  description="Histórico de saldos bancários — evolução mensal e diária para conciliação"
);

-- 4. Categorias contábeis
CREATE TABLE IF NOT EXISTS `studio_koti.categorias` (
  codigo                STRING      NOT NULL OPTIONS(description="Código da categoria (ex: 1.01.010)"),
  nome                  STRING      NOT NULL OPTIONS(description="Nome limpo (sem prefixo numérico)"),
  grupo                 STRING      OPTIONS(description="Código do grupo (2 primeiros níveis, ex: 1.01)"),
  sync_timestamp        TIMESTAMP   NOT NULL
)
OPTIONS(description="Categorias contábeis do Omie (sem prefixo numérico)");

-- 5. Projetos/obras
CREATE TABLE IF NOT EXISTS `studio_koti.projetos` (
  id                    INT64       NOT NULL OPTIONS(description="ID do projeto no Omie"),
  nome                  STRING      NOT NULL OPTIONS(description="Nome do projeto"),
  sync_timestamp        TIMESTAMP   NOT NULL
)
OPTIONS(description="Projetos/obras cadastrados no Omie");

-- 6. Clientes/fornecedores
CREATE TABLE IF NOT EXISTS `studio_koti.clientes` (
  id                    INT64       NOT NULL OPTIONS(description="codigo_cliente_omie"),
  nome_fantasia         STRING      OPTIONS(description="nome_fantasia"),
  razao_social          STRING      OPTIONS(description="razao_social"),
  estado                STRING      OPTIONS(description="UF do cliente"),
  ativo                 BOOL        OPTIONS(description="true se inativo='N'"),
  pessoa_fisica         BOOL        OPTIONS(description="true se pessoa_fisica='S'"),
  data_cadastro         DATE        OPTIONS(description="Data de inclusão no Omie"),
  sync_timestamp        TIMESTAMP   NOT NULL
)
OPTIONS(description="Cadastro completo de clientes/fornecedores do Omie");

-- 7. Pedidos de venda (1 linha por item)
CREATE TABLE IF NOT EXISTS `studio_koti.vendas_pedidos` (
  pedido_id             INT64       OPTIONS(description="ID do pedido no Omie"),
  valor_mercadorias     FLOAT64     NOT NULL OPTIONS(description="Valor total de mercadorias"),
  etapa                 STRING      OPTIONS(description="Etapa: Em Aberto, Aprovado, Faturado, Cancelado, etc."),
  data_previsao         DATE        OPTIONS(description="Data prevista"),
  produto_descricao     STRING      OPTIONS(description="Descrição do produto (explode 1 linha por item)"),
  produto_quantidade    FLOAT64     OPTIONS(description="Quantidade do item"),
  produto_valor_total   FLOAT64     OPTIONS(description="Valor total do item"),
  sync_timestamp        TIMESTAMP   NOT NULL,
  sync_date             DATE        NOT NULL
)
PARTITION BY sync_date
OPTIONS(description="Pedidos de venda do Omie — 1 linha por item de pedido");

-- 8. Orçamento DRE (Real vs BP)
CREATE TABLE IF NOT EXISTS `studio_koti.orcamento_dre` (
  label                 STRING      NOT NULL OPTIONS(description="Nome da linha do DRE (ex: Receita Bruta, EBITDA)"),
  section               STRING      NOT NULL OPTIONS(description="Seção: receita, impostos, custos, margem, sga, ebitda, ll"),
  level                 INT64       NOT NULL OPTIONS(description="Nível: 0=total, 1=subtotal, 2=detalhe"),
  mes                   STRING      NOT NULL OPTIONS(description="Mês no formato YYYY-MM"),
  valor_real            FLOAT64     OPTIONS(description="Valor realizado (aba Realizado da planilha)"),
  valor_bp              FLOAT64     OPTIONS(description="Valor orçado (aba BP da planilha)"),
  variacao_pct          FLOAT64     OPTIONS(description="(real - bp) / abs(bp) * 100, NULL se bp=0"),
  mes_com_real          BOOL        OPTIONS(description="true se este mês tem dados reais"),
  sync_timestamp        TIMESTAMP   NOT NULL
)
OPTIONS(description="DRE Real vs Orçado (Business Plan) — 1 linha por item × mês");

-- 9. Sync log
CREATE TABLE IF NOT EXISTS `studio_koti.sync_log` (
  sync_id               STRING      NOT NULL OPTIONS(description="UUID do sync"),
  started_at            TIMESTAMP   NOT NULL,
  finished_at           TIMESTAMP,
  status                STRING      NOT NULL OPTIONS(description="running, success, failed"),
  duration_seconds      INT64,
  lancamentos_count     INT64,
  saldos_count          INT64,
  clientes_count        INT64,
  projetos_count        INT64,
  categorias_count      INT64,
  error_message         STRING,
  is_incremental        BOOL        OPTIONS(description="true se usou cache")
)
OPTIONS(description="Log de execução de cada sync — para monitoramento e alertas");

-- ============================================================
-- VIEW: Histórico de saldos deduplicado
-- Usar esta view no Looker Studio para evitar duplicatas do WRITE_APPEND
-- ============================================================
CREATE OR REPLACE VIEW `studio_koti.v_historico_saldos` AS
SELECT * EXCEPT(rn) FROM (
  SELECT *,
    ROW_NUMBER() OVER (
      PARTITION BY conta_id, data_referencia, tipo
      ORDER BY sync_timestamp DESC
    ) AS rn
  FROM `studio_koti.historico_saldos`
)
WHERE rn = 1;
