-- =============================================================================
-- VIEW: v_documentos_fiscais_itens_classificados
-- Criada em: 2026-05-25
-- Fases: V3.1 (simulacao) + V3.1b (diagnostico macro) + V3.2 (implementacao)
-- =============================================================================
-- Objetivo: classificacao operacional de itens NF-e por taxonomia V3.
--   Granularidade: 1 linha por item de NF-e.
--   Universo: naturezas compra_com_custo, entrega_futura,
--             entrega_futura_com_financeiro_suspeito.
--   Excluido: sem_financeiro (decisao V3.1-D3.1), faturamento_antecipado,
--             devolucao, bonificacao, indefinido.
--
-- Campos derivados adicionados:
--   tipo_compra_v1       : campo original Omie (preservado como auditoria)
--   tipo_compra_v3       : bucket classificado (14 valores)
--   taxonomia_origem     : nivel da cascata (R1-R5)
--   taxonomia_confidence : confianca (HIGH/MEDIUM/LOW/UNCLASSIFIED)
--   granularidade_analitica : "item" ou "pacote_documento"
--   nao_itemizar         : TRUE somente para marcenaria_pacote
--   nc_real              : TRUE somente para indefinido_revisar
--   natureza_nf, alerta_auditoria : herdados de v_documentos_fiscais_natureza
--   n_id_destinatario, razao_social_destinatario, compl_cod_categ : de documentos_fiscais
--
-- Buckets tipo_compra_v3 (14 valores):
--   civil_obra | decoracao_mobiliario | eletros_e_colchoes | revestimentos
--   hidraulica | marcenaria_pacote | tintas_acabamentos | operacional_consumo
--   ferragens | iluminacao | eletrica_baixa_tensao | logistica_montagem
--   ativo_imobilizado | indefinido_revisar
--
-- Cascata R1 -> R2 -> R3 -> R4 -> R5 (primeira regra que casa vence):
--   R1: Fornecedor mono-segmento (~0% falso positivo, HIGH confidence)
--   R2: Categoria Omie do documento (compl_cod_categ)
--   R3: NCM-4 (4 primeiros digitos)
--   R4: Keyword em x_prod (UPPER)
--   R5: indefinido_revisar (fallback)
--
-- Regra de nao-itemizacao (decisao V3.1-D3.3):
--   marcenaria_pacote deve ser agregado por NF em dashboards de custo.
--   nao_itemizar=TRUE e o sinal para o dashboard.
--
-- Rollback: DROP VIEW dashboard-koti-omie.studio_koti.v_documentos_fiscais_itens_classificados
-- =============================================================================

CREATE OR REPLACE VIEW `dashboard-koti-omie.studio_koti.v_documentos_fiscais_itens_classificados`
AS
WITH

naturezas_ok AS (
  SELECT n_id_receb, natureza_nf, alerta_auditoria
  FROM `dashboard-koti-omie.studio_koti.v_documentos_fiscais_natureza`
  WHERE natureza_nf IN (
    'compra_com_custo',
    'entrega_futura',
    'entrega_futura_com_financeiro_suspeito'
  )
),

base AS (
  SELECT
    i.n_id_receb,       i.n_sequencia,          i.data_emissao,
    i.x_prod,           i.ncm,                  i.cfop,
    i.c_prod,           i.c_ean,                i.c_origem,
    i.u_com,            i.q_com,                i.v_un_com,
    i.v_prod,           i.v_tot_item,           i.v_desc,
    i.v_frete,          i.p_icms,               i.v_icms,
    i.v_bc_icms,        i.p_pis,                i.v_pis,
    i.p_cofins,         i.v_cofins,             i.v_bc_st,
    i.v_bc_ipi,         i.c_descricao_produto,  i.c_codigo_produto,
    i.c_unidade_nfe,    i.n_id_produto,         i.n_id_item,
    i.c_ignorar_item,   i.categoria_cfop,       i.grupo_ncm,
    i.tipo_compra       AS tipo_compra_v1,
    i.synced_at,
    df.n_id_destinatario,
    df.razao_social_destinatario,
    df.compl_cod_categ,
    n.natureza_nf,
    n.alerta_auditoria,
    LEFT(COALESCE(i.ncm, ''), 4)      AS ncm4,
    UPPER(COALESCE(i.x_prod, ''))     AS xpu
  FROM `dashboard-koti-omie.studio_koti.documentos_fiscais_itens` i
  JOIN `dashboard-koti-omie.studio_koti.documentos_fiscais`        df USING (n_id_receb)
  JOIN naturezas_ok                                                 n  USING (n_id_receb)
),

-- CTE que aplica a cascata; derivados (nao_itemizar, nc_real) calculados no
-- SELECT final a partir de tipo_compra_v3 para garantir consistencia.
classified AS (
  SELECT
    *,

    -- ================================================================
    -- tipo_compra_v3: bucket da cascata V3
    -- ================================================================
    CASE
      -- ===== R1: Fornecedor mono-segmento =====
      WHEN razao_social_destinatario = 'NICOM COMERCIO DE MATERIAIS PARA CONSTRUCOES LTDA'
           THEN 'civil_obra'
      WHEN razao_social_destinatario = 'NORTE SUL INDUSTRIA DE MOVEIS LTDA'
           THEN 'marcenaria_pacote'
      WHEN razao_social_destinatario IN (
           'MULTITINTAS COMERCIO DE TINTAS LTDA',
           'BRASILUX IND COM IMP EXP LTDA')
           THEN 'tintas_acabamentos'
      WHEN razao_social_destinatario IN (
           'D JUAN COLCHOES INDUSTRIA, COMERCIO E SERVICOS TEXTEIS LTDA',
           'Electrolux do Brasil S/A',
           'UNIAR COMERCIO DE ELETRO-ELETRONICOS E SERVICOS LTDA')
           THEN 'eletros_e_colchoes'
      WHEN razao_social_destinatario LIKE 'ASSA ABLOY%'
           THEN 'ferragens'
      WHEN razao_social_destinatario IN (
           'M C COMERCIO DE GESSO LTDA',
           'R L DA SILVA COMERCIO DE GESSO ME')
           THEN 'civil_obra'
      WHEN razao_social_destinatario IN (
           'PORTO ABC - REVESTIMENTOS E DECORACOES LTDA',
           'COMPANHIA BRASILEIRA DE CERAMICA S.A.',
           'Esquadrifenix Comercio de Vidros e Instalacao')
           THEN 'revestimentos'
      WHEN razao_social_destinatario = 'Dexco S.A.'
           THEN 'hidraulica'
      WHEN razao_social_destinatario IN (
           'METTA MOVEIS INDUSTRIA E COMERCIO LTDA',
           'IMD COMERCIO DE MOVEIS LTDA',
           'ARTIMAGE IND. E COM. EIRELI',
           'TAPETES SAO CARLOS LTDA.',
           'Ester Adriana Ferreira de Almeida',
           'GALERIA GABRIELA NORA COMERCIO DE FLORES E DECORACAO LTDA')
           THEN 'decoracao_mobiliario'

      -- ===== R2: Categoria Omie do documento =====
      WHEN compl_cod_categ IN ('2.01.01', '2.01.85', '2.01.89') THEN 'civil_obra'
      WHEN compl_cod_categ = '2.01.90'                           THEN 'decoracao_mobiliario'
      WHEN compl_cod_categ IN ('2.01.94', '2.01.93', '2.01.03') THEN 'revestimentos'
      WHEN compl_cod_categ = '2.01.92'                           THEN 'marcenaria_pacote'
      WHEN compl_cod_categ IN ('2.01.88', '2.01.87', '2.01.96') THEN 'eletros_e_colchoes'
      WHEN compl_cod_categ = '2.01.95'                           THEN 'iluminacao'
      WHEN compl_cod_categ = '2.01.02'                           THEN 'hidraulica'
      WHEN compl_cod_categ = '2.01.86'                           THEN 'ferragens'
      WHEN compl_cod_categ = '2.01.98'                           THEN 'logistica_montagem'
      WHEN compl_cod_categ IN ('2.07.01', '2.07.04')             THEN 'ativo_imobilizado'
      WHEN compl_cod_categ LIKE '2.04.%'                         THEN 'operacional_consumo'

      -- ===== R3: NCM-4 =====
      WHEN ncm4 IN ('6907', '6802', '6801')                      THEN 'revestimentos'
      WHEN ncm4 IN ('6809', '6810', '3214', '7216', '4418', '4407', '4409')
           THEN 'civil_obra'
      WHEN ncm4 = '9403'                                         THEN 'marcenaria_pacote'
      WHEN ncm4 IN ('9401', '9404', '5703', '5702', '5311', '9701', '6302', '0601', '0602')
           THEN 'decoracao_mobiliario'
      WHEN ncm4 = '9405'                                         THEN 'iluminacao'
      WHEN ncm4 IN ('8544', '3925', '8536', '8537', '8538')      THEN 'eletrica_baixa_tensao'
      WHEN ncm4 = '3917' AND xpu LIKE '%CAIXA%'                  THEN 'eletrica_baixa_tensao'
      WHEN ncm4 IN ('8516', '8415', '8418', '8422', '8524', '8528')
           THEN 'eletros_e_colchoes'
      WHEN ncm4 IN ('8481', '6910', '7412')                      THEN 'hidraulica'
      WHEN ncm4 IN ('8301', '7323', '7324', '7418')              THEN 'ferragens'
      WHEN ncm4 = '3209'                                         THEN 'tintas_acabamentos'
      WHEN ncm4 IN (
           '2201','2202','4818','4901','4802','3923','8443','8508',
           '0901','1905','2101','3307','3402','8506','1704','9603',
           '1904','2007','1806','2008','3405','3924','2203','8517',
           '8471','3921','3506','4819','6114','6405')
           THEN 'operacional_consumo'

      -- ===== R4: Keyword em x_prod =====
      WHEN xpu LIKE '%PORTA TOALHA%' OR xpu LIKE '%PORTA-TOALHA%'
           OR xpu LIKE '%SABONETEIRA%'
           THEN 'ferragens'
      WHEN xpu LIKE '%PAPELEIRA%' OR xpu LIKE '%SUPORTE PAPEL%'
           THEN 'ferragens'
      WHEN xpu LIKE '%CORTINA%' OR xpu LIKE '%PERSIANA%'
           THEN 'decoracao_mobiliario'
      WHEN xpu LIKE '%SPOT%' OR xpu LIKE '%ARANDELA%'
           THEN 'iluminacao'
      WHEN xpu LIKE '%DISJUNTOR%' OR xpu LIKE '%QUADRO ELETRIC%'
           THEN 'eletrica_baixa_tensao'

      -- ===== R5: Fallback =====
      ELSE 'indefinido_revisar'
    END AS tipo_compra_v3,

    -- ================================================================
    -- taxonomia_origem: nivel que efetivamente classificou
    -- ================================================================
    CASE
      WHEN razao_social_destinatario IN (
           'NICOM COMERCIO DE MATERIAIS PARA CONSTRUCOES LTDA',
           'NORTE SUL INDUSTRIA DE MOVEIS LTDA',
           'MULTITINTAS COMERCIO DE TINTAS LTDA','BRASILUX IND COM IMP EXP LTDA',
           'D JUAN COLCHOES INDUSTRIA, COMERCIO E SERVICOS TEXTEIS LTDA',
           'Electrolux do Brasil S/A','UNIAR COMERCIO DE ELETRO-ELETRONICOS E SERVICOS LTDA',
           'M C COMERCIO DE GESSO LTDA','R L DA SILVA COMERCIO DE GESSO ME',
           'PORTO ABC - REVESTIMENTOS E DECORACOES LTDA',
           'COMPANHIA BRASILEIRA DE CERAMICA S.A.',
           'Esquadrifenix Comercio de Vidros e Instalacao','Dexco S.A.',
           'METTA MOVEIS INDUSTRIA E COMERCIO LTDA','IMD COMERCIO DE MOVEIS LTDA',
           'ARTIMAGE IND. E COM. EIRELI','TAPETES SAO CARLOS LTDA.',
           'Ester Adriana Ferreira de Almeida',
           'GALERIA GABRIELA NORA COMERCIO DE FLORES E DECORACAO LTDA'
      ) OR razao_social_destinatario LIKE 'ASSA ABLOY%'
           THEN 'R1_fornecedor'
      WHEN compl_cod_categ IN (
           '2.01.01','2.01.85','2.01.89','2.01.90','2.01.94','2.01.93','2.01.03',
           '2.01.92','2.01.88','2.01.87','2.01.96','2.01.95','2.01.02','2.01.86',
           '2.01.98','2.07.01','2.07.04'
      ) OR compl_cod_categ LIKE '2.04.%'
           THEN 'R2_categoria_omie'
      WHEN ncm IS NOT NULL AND ncm != '' AND (
           ncm4 IN (
             '6907','6802','6801','6809','6810','3214','7216','4418','4407','4409',
             '9403','9401','9404','5703','5702','5311','9701','6302','0601','0602',
             '9405','8544','3925','8536','8537','8538','8516','8415','8418','8422',
             '8524','8528','8481','6910','7412','8301','7323','7324','7418','3209',
             '2201','2202','4818','4901','4802','3923','8443','8508','0901','1905',
             '2101','3307','3402','8506','1704','9603','1904','2007','1806','2008',
             '3405','3924','2203','8517','8471','3921','3506','4819','6114','6405'
           ) OR (ncm4 = '3917' AND xpu LIKE '%CAIXA%')
      ) THEN 'R3_ncm'
      WHEN (
           xpu LIKE '%PORTA TOALHA%' OR xpu LIKE '%PORTA-TOALHA%'
           OR xpu LIKE '%SABONETEIRA%' OR xpu LIKE '%PAPELEIRA%'
           OR xpu LIKE '%SUPORTE PAPEL%' OR xpu LIKE '%CORTINA%'
           OR xpu LIKE '%PERSIANA%' OR xpu LIKE '%SPOT%'
           OR xpu LIKE '%ARANDELA%' OR xpu LIKE '%DISJUNTOR%'
           OR xpu LIKE '%QUADRO ELETRIC%'
      ) THEN 'R4_keyword'
      ELSE 'R5_indefinido'
    END AS taxonomia_origem,

    -- ================================================================
    -- taxonomia_confidence: confianca da classificacao
    -- ================================================================
    CASE
      WHEN razao_social_destinatario IN (
           'NICOM COMERCIO DE MATERIAIS PARA CONSTRUCOES LTDA',
           'NORTE SUL INDUSTRIA DE MOVEIS LTDA',
           'MULTITINTAS COMERCIO DE TINTAS LTDA','BRASILUX IND COM IMP EXP LTDA',
           'D JUAN COLCHOES INDUSTRIA, COMERCIO E SERVICOS TEXTEIS LTDA',
           'Electrolux do Brasil S/A','UNIAR COMERCIO DE ELETRO-ELETRONICOS E SERVICOS LTDA',
           'M C COMERCIO DE GESSO LTDA','R L DA SILVA COMERCIO DE GESSO ME',
           'PORTO ABC - REVESTIMENTOS E DECORACOES LTDA',
           'COMPANHIA BRASILEIRA DE CERAMICA S.A.',
           'Esquadrifenix Comercio de Vidros e Instalacao','Dexco S.A.',
           'METTA MOVEIS INDUSTRIA E COMERCIO LTDA','IMD COMERCIO DE MOVEIS LTDA',
           'ARTIMAGE IND. E COM. EIRELI','TAPETES SAO CARLOS LTDA.',
           'Ester Adriana Ferreira de Almeida',
           'GALERIA GABRIELA NORA COMERCIO DE FLORES E DECORACAO LTDA'
      ) OR razao_social_destinatario LIKE 'ASSA ABLOY%'
           THEN 'HIGH'
      WHEN compl_cod_categ IN (
           '2.01.01','2.01.85','2.01.89','2.01.90','2.01.94','2.01.93','2.01.03',
           '2.01.92','2.01.88','2.01.87','2.01.96','2.01.95','2.01.02','2.01.86',
           '2.01.98','2.07.01','2.07.04'
      ) OR compl_cod_categ LIKE '2.04.%'
           THEN 'MEDIUM'
      WHEN ncm IS NOT NULL AND ncm != '' AND (
           ncm4 IN (
             '6907','6802','6801','6809','6810','3214','7216','4418','4407','4409',
             '9403','9401','9404','5703','5702','5311','9701','6302','0601','0602',
             '9405','8544','3925','8536','8537','8538','8516','8415','8418','8422',
             '8524','8528','8481','6910','7412','8301','7323','7324','7418','3209',
             '2201','2202','4818','4901','4802','3923','8443','8508','0901','1905',
             '2101','3307','3402','8506','1704','9603','1904','2007','1806','2008',
             '3405','3924','2203','8517','8471','3921','3506','4819','6114','6405'
           ) OR (ncm4 = '3917' AND xpu LIKE '%CAIXA%')
      ) THEN 'MEDIUM'
      WHEN (
           xpu LIKE '%PORTA TOALHA%' OR xpu LIKE '%PORTA-TOALHA%'
           OR xpu LIKE '%SABONETEIRA%' OR xpu LIKE '%PAPELEIRA%'
           OR xpu LIKE '%SUPORTE PAPEL%' OR xpu LIKE '%CORTINA%'
           OR xpu LIKE '%PERSIANA%' OR xpu LIKE '%SPOT%'
           OR xpu LIKE '%ARANDELA%' OR xpu LIKE '%DISJUNTOR%'
           OR xpu LIKE '%QUADRO ELETRIC%'
      ) THEN 'LOW'
      ELSE 'UNCLASSIFIED'
    END AS taxonomia_confidence

  FROM base
)

-- ============================================================
-- SELECT final: campos base + derivados de tipo_compra_v3
-- (nao_itemizar, granularidade_analitica, nc_real derivados
--  de tipo_compra_v3 para garantir consistencia)
-- ============================================================
SELECT
  n_id_receb,           n_sequencia,          data_emissao,
  x_prod,               ncm,                  cfop,
  c_prod,               c_ean,                c_origem,
  u_com,                q_com,                v_un_com,
  v_prod,               v_tot_item,           v_desc,
  v_frete,              p_icms,               v_icms,
  v_bc_icms,            p_pis,                v_pis,
  p_cofins,             v_cofins,             v_bc_st,
  v_bc_ipi,             c_descricao_produto,  c_codigo_produto,
  c_unidade_nfe,        n_id_produto,         n_id_item,
  c_ignorar_item,       categoria_cfop,       grupo_ncm,
  tipo_compra_v1,       synced_at,
  n_id_destinatario,    razao_social_destinatario,  compl_cod_categ,
  natureza_nf,          alerta_auditoria,

  -- Classificacao V3
  tipo_compra_v3,
  taxonomia_origem,
  taxonomia_confidence,

  -- Derivados diretos de tipo_compra_v3 (sem repeticao de logica)
  CASE tipo_compra_v3
    WHEN 'marcenaria_pacote' THEN 'pacote_documento'
    ELSE                          'item'
  END                                                   AS granularidade_analitica,

  (tipo_compra_v3 = 'marcenaria_pacote')                AS nao_itemizar,

  (tipo_compra_v3 = 'indefinido_revisar')               AS nc_real

FROM classified
;
