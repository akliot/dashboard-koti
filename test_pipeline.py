#!/usr/bin/env python3
"""
Testes unitários para o pipeline omie_sync_bq.py.

Uso:
  python3 -m pytest test_pipeline.py -v
  python3 test_pipeline.py
"""

import unittest
import os
import sys

os.environ.setdefault("GCP_PROJECT_ID", "dashboard-koti-omie")
os.environ.setdefault("BQ_DATASET", "studio_koti")
os.environ.setdefault("OMIE_APP_KEY", "test")
os.environ.setdefault("OMIE_APP_SECRET", "test")

import omie_sync_bq as pipeline


class TestParseDate(unittest.TestCase):
    """Testa parsing de datas da API Omie (DD/MM/YYYY → YYYY-MM-DD)."""

    def test_valido(self):
        self.assertEqual(pipeline.parse_date("15/03/2026"), "2026-03-15")

    def test_primeiro_dia(self):
        self.assertEqual(pipeline.parse_date("01/01/2026"), "2026-01-01")

    def test_ultimo_dia(self):
        self.assertEqual(pipeline.parse_date("31/12/2025"), "2025-12-31")

    def test_vazio(self):
        self.assertIsNone(pipeline.parse_date(""))

    def test_none(self):
        self.assertIsNone(pipeline.parse_date(None))

    def test_formato_errado_iso(self):
        self.assertIsNone(pipeline.parse_date("2026-03-15"))

    def test_formato_errado_americano(self):
        self.assertIsNone(pipeline.parse_date("03/15/2026"))

    def test_lixo(self):
        self.assertIsNone(pipeline.parse_date("abc"))


class TestLimparNomeCategoria(unittest.TestCase):
    """Testa remoção de prefixo numérico de categorias."""

    def test_com_prefixo(self):
        self.assertEqual(pipeline._limpar_nome_categoria("4.01.010 - Marcenaria"), "Marcenaria")

    def test_com_prefixo_longo(self):
        self.assertEqual(pipeline._limpar_nome_categoria("2.01.03 - Mão de Obra"), "Mão de Obra")

    def test_sem_prefixo(self):
        self.assertEqual(pipeline._limpar_nome_categoria("Marcenaria"), "Marcenaria")

    def test_vazio(self):
        self.assertEqual(pipeline._limpar_nome_categoria(""), "")

    def test_so_prefixo(self):
        self.assertEqual(pipeline._limpar_nome_categoria("1.01 - "), "")

    def test_disponivel_mantido(self):
        # "<Disponível>" é filtrado em outro lugar, não aqui
        result = pipeline._limpar_nome_categoria("1.01 - <Disponível>")
        self.assertEqual(result, "<Disponível>")


class TestContasIgnorar(unittest.TestCase):
    """Testa que CONTAS_IGNORAR está configurada."""

    def test_contas_ignorar_existe(self):
        self.assertIsInstance(pipeline.CONTAS_IGNORAR, set)

    def test_baixa_nfs_na_lista(self):
        self.assertIn(8754849088, pipeline.CONTAS_IGNORAR)


class TestFaturamentoDireto(unittest.TestCase):
    """Testa lógica is_faturamento_direto (⚡ Koti-specific)."""

    def test_saida_com_fd_no_documento(self):
        """Saída com 'FD' no numero_documento = True."""
        num_doc = "FD NF 77.182"
        is_fd = "fd" in num_doc.lower()
        self.assertTrue(is_fd)

    def test_saida_sem_fd(self):
        """Saída sem 'FD' = False."""
        num_doc = "000077971/1"
        is_fd = "fd" in num_doc.lower()
        self.assertFalse(is_fd)

    def test_saida_fd_case_insensitive(self):
        """FD deve ser case-insensitive."""
        for doc in ["FD 123", "fd 123", "Fd NFS 9", "abc FD xyz"]:
            self.assertTrue("fd" in doc.lower(), f"Falhou para: {doc}")

    def test_entrada_com_faturamento_direto(self):
        """Entrada com categoria 'Faturamento Direto' = True."""
        cat_nome = "Faturamento Direto"
        is_fd = "faturamento direto" in (cat_nome or "").lower()
        self.assertTrue(is_fd)

    def test_entrada_sem_faturamento_direto(self):
        """Entrada com outra categoria = False."""
        cat_nome = "Marcenaria"
        is_fd = "faturamento direto" in (cat_nome or "").lower()
        self.assertFalse(is_fd)

    def test_entrada_cat_none(self):
        """Entrada com categoria None = False."""
        cat_nome = None
        is_fd = "faturamento direto" in (cat_nome or "").lower()
        self.assertFalse(is_fd)


class TestDataPagamento(unittest.TestCase):
    """Testa extração de data_pagamento (info.dAlt)."""

    def _extract(self, record: dict) -> str | None:
        """Replica a lógica de _extract_data_pagamento."""
        status = (record.get("status_titulo", "") or "").upper()
        if status not in ("PAGO", "RECEBIDO", "LIQUIDADO"):
            return None
        info = record.get("info", {}) or {}
        d_alt = info.get("dAlt", "")
        return pipeline.parse_date(d_alt) if d_alt else None

    def test_pago_com_data(self):
        r = {"status_titulo": "PAGO", "info": {"dAlt": "16/03/2026"}}
        self.assertEqual(self._extract(r), "2026-03-16")

    def test_recebido_com_data(self):
        r = {"status_titulo": "RECEBIDO", "info": {"dAlt": "10/03/2026"}}
        self.assertEqual(self._extract(r), "2026-03-10")

    def test_liquidado_com_data(self):
        r = {"status_titulo": "LIQUIDADO", "info": {"dAlt": "05/01/2026"}}
        self.assertEqual(self._extract(r), "2026-01-05")

    def test_a_vencer_sem_data(self):
        r = {"status_titulo": "A VENCER", "info": {"dAlt": "20/03/2026"}}
        self.assertIsNone(self._extract(r))

    def test_atrasado_sem_data(self):
        r = {"status_titulo": "ATRASADO", "info": {"dAlt": "15/02/2026"}}
        self.assertIsNone(self._extract(r))

    def test_pago_sem_info(self):
        r = {"status_titulo": "PAGO"}
        self.assertIsNone(self._extract(r))

    def test_pago_info_vazio(self):
        r = {"status_titulo": "PAGO", "info": {}}
        self.assertIsNone(self._extract(r))

    def test_pago_info_none(self):
        r = {"status_titulo": "PAGO", "info": None}
        self.assertIsNone(self._extract(r))

    def test_status_vazio(self):
        r = {"status_titulo": "", "info": {"dAlt": "16/03/2026"}}
        self.assertIsNone(self._extract(r))


class TestStatusMapping(unittest.TestCase):
    """Testa mapeamento e normalização de status."""

    def test_uppercase(self):
        for raw, expected in [("Pago", "PAGO"), ("recebido", "RECEBIDO"),
                              ("A Vencer", "A VENCER"), ("ATRASADO", "ATRASADO")]:
            result = (raw or "").upper()
            self.assertEqual(result, expected)

    def test_vazio(self):
        self.assertEqual(("" or "").upper(), "")

    def test_none(self):
        self.assertEqual((None or "").upper(), "")


class TestCategoriaGrupo(unittest.TestCase):
    """Testa extração de grupo de categoria (2 primeiros níveis)."""

    def test_tres_niveis(self):
        cat_cod = "4.01.010"
        grupo = ".".join(cat_cod.split(".")[:2])
        self.assertEqual(grupo, "4.01")

    def test_dois_niveis(self):
        cat_cod = "2.01"
        grupo = ".".join(cat_cod.split(".")[:2])
        self.assertEqual(grupo, "2.01")

    def test_um_nivel(self):
        cat_cod = "1"
        grupo = ".".join(cat_cod.split(".")[:2])
        self.assertEqual(grupo, "1")

    def test_vazio(self):
        cat_cod = ""
        grupo = ".".join(cat_cod.split(".")[:2]) if cat_cod else None
        self.assertIsNone(grupo)


class TestTableRef(unittest.TestCase):
    """Testa construção de referências de tabela."""

    def test_table_ref(self):
        ref = pipeline.table_ref("lancamentos")
        self.assertIn("studio_koti", ref)
        self.assertIn("lancamentos", ref)
        self.assertIn("dashboard-koti-omie", ref)


class TestIsSafeSql(unittest.TestCase):
    """Testa validação de SQL (bot usa isso para segurança)."""

    def _check(self, sql: str) -> bool:
        """Replica is_safe_sql do bot."""
        sql_upper = sql.upper().strip()
        if not sql_upper.startswith("SELECT"):
            return False
        dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "MERGE"]
        words = sql_upper.split()
        for word in dangerous:
            if word in words:
                return False
        if "studio_koti" not in sql:
            return False
        return True

    def test_select_valido(self):
        self.assertTrue(self._check("SELECT * FROM `dashboard-koti-omie.studio_koti.lancamentos`"))

    def test_insert_bloqueado(self):
        self.assertFalse(self._check("INSERT INTO `studio_koti.lancamentos` VALUES (1)"))

    def test_delete_bloqueado(self):
        self.assertFalse(self._check("DELETE FROM `studio_koti.lancamentos`"))

    def test_drop_bloqueado(self):
        self.assertFalse(self._check("DROP TABLE `studio_koti.lancamentos`"))

    def test_sem_dataset(self):
        self.assertFalse(self._check("SELECT * FROM `outro_dataset.lancamentos`"))

    def test_update_bloqueado(self):
        self.assertFalse(self._check("UPDATE `studio_koti.lancamentos` SET valor=0"))


class TestDreMapValidation(unittest.TestCase):
    """Testa validação do DRE_MAP do extract_bp_bq."""

    def test_validate_dre_map_all_match(self):
        """Quando labels batem, retorna 0 mismatches."""
        import extract_bp_bq as bp

        class FakeWS:
            def __init__(self, mapping):
                self._mapping = mapping
            def cell(self, row, column):
                class C:
                    def __init__(self, v):
                        self.value = v
                return C(self._mapping.get((row, column)))

        # Montar fake worksheets separadas para Real e BP
        real_mapping = {}
        bp_mapping = {}
        for real_row, bp_row, label, section, level in bp.DRE_MAP:
            if real_row is not None:
                real_mapping[(real_row, 2)] = label
            if bp_row is not None:
                bp_mapping[(bp_row, 2)] = label

        ws_real = FakeWS(real_mapping)
        ws_bp = FakeWS(bp_mapping)
        self.assertEqual(bp.validate_dre_map(ws_real, ws_bp), 0)

    def test_validate_dre_map_some_mismatch(self):
        """Quando labels não batem, retorna contagem de mismatches."""
        import extract_bp_bq as bp

        class FakeWS:
            def cell(self, row, column):
                class C:
                    def __init__(self):
                        self.value = "ERRADO"
                return C()

        ws = FakeWS()
        mismatches = bp.validate_dre_map(ws, ws)
        self.assertGreater(mismatches, 0)

    def test_dre_map_structure(self):
        """DRE_MAP tem a estrutura correta (5 campos por entrada)."""
        import extract_bp_bq as bp
        for entry in bp.DRE_MAP:
            self.assertEqual(len(entry), 5, f"Entry should have 5 fields: {entry}")
            real_row, bp_row, label, section, level = entry
            self.assertIsInstance(label, str)
            self.assertIn(level, (0, 1, 2))

    def test_dre_map_has_receita_bruta(self):
        """DRE_MAP deve começar com Receita Bruta."""
        import extract_bp_bq as bp
        self.assertEqual(bp.DRE_MAP[0][2], "Receita Bruta")

    def test_dre_map_has_lucro_liquido(self):
        """DRE_MAP deve terminar com Lucro Líquido."""
        import extract_bp_bq as bp
        self.assertEqual(bp.DRE_MAP[-1][2], "Lucro Líquido")


class TestDocumentosFiscaisV1(unittest.TestCase):
    """Testa funções isoladas do pipeline de NF-e recebidas (Fase 5)."""

    # ------------------------------------------------------------------
    # _classificar_cfop
    # ------------------------------------------------------------------

    def test_cfop_compra_revenda(self):
        self.assertEqual(pipeline._classificar_cfop("1102"), "compra_revenda")

    def test_cfop_compra_revenda_2xxx(self):
        self.assertEqual(pipeline._classificar_cfop("2102"), "compra_revenda")

    def test_cfop_uso_consumo(self):
        self.assertEqual(pipeline._classificar_cfop("1403"), "uso_consumo")

    def test_cfop_ativo_imobilizado(self):
        self.assertEqual(pipeline._classificar_cfop("1556"), "ativo_imobilizado")

    def test_cfop_remessa_passagem(self):
        self.assertEqual(pipeline._classificar_cfop("1922"), "remessa_passagem")

    def test_cfop_importacao_3xxx(self):
        self.assertEqual(pipeline._classificar_cfop("3102"), "importacao")

    def test_cfop_devolucao(self):
        self.assertEqual(pipeline._classificar_cfop("1201"), "devolucao_venda")

    def test_cfop_desconhecido(self):
        self.assertEqual(pipeline._classificar_cfop("9999"), "outros")

    def test_cfop_vazio(self):
        self.assertEqual(pipeline._classificar_cfop(""), "outros")

    def test_cfop_none(self):
        self.assertEqual(pipeline._classificar_cfop(None), "outros")

    # ------------------------------------------------------------------
    # _classificar_ncm
    # ------------------------------------------------------------------

    def test_ncm_moveis(self):
        self.assertEqual(pipeline._classificar_ncm("94036000"), "moveis_iluminacao")

    def test_ncm_ferro(self):
        self.assertEqual(pipeline._classificar_ncm("73089000"), "ferro_aco_metais")

    def test_ncm_madeira(self):
        self.assertEqual(pipeline._classificar_ncm("44079100"), "madeira")

    def test_ncm_eletrico(self):
        self.assertEqual(pipeline._classificar_ncm("85044010"), "eletrico_eletronico")

    def test_ncm_maquinas(self):
        self.assertEqual(pipeline._classificar_ncm("84713019"), "maquinas_equipamentos")

    def test_ncm_desconhecido(self):
        self.assertEqual(pipeline._classificar_ncm("01011010"), "outros")

    def test_ncm_vazio(self):
        self.assertEqual(pipeline._classificar_ncm(""), "outros")

    def test_ncm_none(self):
        self.assertEqual(pipeline._classificar_ncm(None), "outros")

    # ------------------------------------------------------------------
    # _classificar_tipo_compra (CFOP + NCM → tipo_compra)
    # ------------------------------------------------------------------

    def test_tipo_material_projeto(self):
        cc, gn, tc = pipeline._classificar_tipo_compra("1102", "94036000")
        self.assertEqual(tc, "material_projeto")
        self.assertEqual(cc, "compra_revenda")
        self.assertEqual(gn, "moveis_iluminacao")

    def test_tipo_ferragem_obra(self):
        _, _, tc = pipeline._classificar_tipo_compra("2102", "73089000")
        self.assertEqual(tc, "ferragem_obra")

    def test_tipo_equipamento_projeto(self):
        _, _, tc = pipeline._classificar_tipo_compra("1102", "85044010")
        self.assertEqual(tc, "equipamento_projeto")

    def test_tipo_mercadoria_geral(self):
        _, _, tc = pipeline._classificar_tipo_compra("1102", "01011010")
        self.assertEqual(tc, "mercadoria_geral")

    def test_tipo_devolucao(self):
        _, _, tc = pipeline._classificar_tipo_compra("1201", "94036000")
        self.assertEqual(tc, "devolucao")

    def test_tipo_ativo_imobilizado(self):
        _, _, tc = pipeline._classificar_tipo_compra("1556", "84713019")
        self.assertEqual(tc, "ativo_imobilizado")

    def test_tipo_importacao(self):
        _, _, tc = pipeline._classificar_tipo_compra("3102", "94036000")
        self.assertEqual(tc, "importacao")

    def test_tipo_uso_consumo(self):
        _, _, tc = pipeline._classificar_tipo_compra("1403", "01011010")
        self.assertEqual(tc, "uso_consumo_operacional")

    def test_tipo_remessa(self):
        _, _, tc = pipeline._classificar_tipo_compra("1922", "73089000")
        self.assertEqual(tc, "remessa_passagem")

    def test_tipo_nao_classificado(self):
        _, _, tc = pipeline._classificar_tipo_compra("1949", "94036000")
        self.assertEqual(tc, "nao_classificado")

    # ------------------------------------------------------------------
    # _normalizar_titulo_fiscal — chave (n_id_receb, n_cod_titulo)
    # ------------------------------------------------------------------

    def test_titulo_chave_composta(self):
        tit = {
            "nCodTitulo": 999001,
            "nParcela": 1,
            "nTotParc": 2,
            "nCodProjeto": 42,
            "cCodCateg": "2.01.03",
            "cDoc": "NF-001",
            "cNumTitulo": "001/1",
            "dDtEmissao": "10/04/2026",
            "dDtVenc": "10/05/2026",
            "dDtPrevisao": "10/05/2026",
            "nValorTitulo": 1500.0,
        }
        row = pipeline._normalizar_titulo_fiscal(88888, tit, "2026-05-17T00:00:00")
        self.assertEqual(row["n_id_receb"], 88888)
        self.assertEqual(row["n_cod_titulo"], 999001)
        self.assertEqual(row["dt_emissao"], "2026-04-10")
        self.assertEqual(row["dt_vencimento"], "2026-05-10")
        self.assertEqual(row["n_valor_titulo"], 1500.0)
        self.assertEqual(row["n_cod_projeto"], 42)

    def test_titulo_sem_datas(self):
        tit = {"nCodTitulo": 123, "nValorTitulo": 500.0}
        row = pipeline._normalizar_titulo_fiscal(1, tit, "2026-05-17T00:00:00")
        self.assertIsNone(row["dt_emissao"])
        self.assertIsNone(row["dt_vencimento"])

    def test_titulo_campos_ausentes_viram_none(self):
        row = pipeline._normalizar_titulo_fiscal(1, {}, "now")
        self.assertIsNone(row["n_cod_titulo"])
        self.assertIsNone(row["n_cod_projeto"])

    # ------------------------------------------------------------------
    # _normalizar_item_fiscal — classificacao embutida
    # ------------------------------------------------------------------

    def test_item_classificacao_propagada(self):
        det = {
            "nItem": 1,
            "prod": {
                "xProd": "Fechadura Inox 304",
                "NCM": "83018090",
                "CFOP": "2102",
                "cProd": "FECH-001",
                "qCom": 10.0,
                "vUnCom": 45.00,
                "vProd": 450.00,
                "vTotItem": 450.00,
            },
        }
        row = pipeline._normalizar_item_fiscal(77777, det, {}, "2026-04-15", "2026-05-17T00:00:00")
        # CFOP 2102 = compra_revenda, NCM 83 = outros → mercadoria_geral
        self.assertEqual(row["cfop"], "2102")
        self.assertEqual(row["ncm"], "83018090")
        self.assertEqual(row["tipo_compra"], "mercadoria_geral")
        self.assertEqual(row["n_id_receb"], 77777)
        self.assertEqual(row["data_emissao"], "2026-04-15")

    def test_item_enriquecimento_omie(self):
        det = {"nItem": 2, "prod": {"xProd": "Dobradiça", "NCM": "73182100", "CFOP": "2102"}}
        enrich = {"cDescricaoProduto": "DOBRADIÇA 304", "nIdProduto": 12345, "cIgnorarItem": "N"}
        row = pipeline._normalizar_item_fiscal(1, det, enrich, "2026-04-15", "now")
        self.assertEqual(row["c_descricao_produto"], "DOBRADIÇA 304")
        self.assertEqual(row["n_id_produto"], 12345)
        self.assertEqual(row["c_ignorar_item"], "N")

    def test_item_n_sequencia_passado_via_seq(self):
        """seq=3 deve aparecer em n_sequencia; nItem no det não interfere."""
        det = {"prod": {"xProd": "Parafuso", "NCM": "73181500", "CFOP": "2102"}}
        row = pipeline._normalizar_item_fiscal(1, det, {}, "2026-04-15", "now", seq=3)
        self.assertEqual(row["n_sequencia"], 3)

    def test_item_n_sequencia_none_sem_seq(self):
        """Sem seq, n_sequencia deve ser None (não crashar)."""
        det = {"prod": {"xProd": "Parafuso", "NCM": "73181500", "CFOP": "2102"}}
        row = pipeline._normalizar_item_fiscal(1, det, {}, "2026-04-15", "now")
        self.assertIsNone(row["n_sequencia"])

    def test_coletar_sequencia_positional(self):
        """coletar_documentos_fiscais deve gerar n_sequencia 1,2,3 por posição no det[]."""
        import unittest.mock as mock

        def make_det(n_cod_item):
            return {
                "nfProdInt": {"nCodItem": n_cod_item},
                "prod": {"xProd": "X", "NCM": "73181500", "CFOP": "2102"},
            }

        nf_stub = {
            "compl": {"nIdReceb": 99},
            "ide": {"tpNF": 0, "dEmi": "15/04/2026"},
            "nfDestInt": {}, "nfEmitInt": {},
            "det": [make_det(1001), make_det(1002), make_det(1003)],
            "titulos": [],
        }
        rec_stub = {
            "cabec": {}, "totais": {}, "infoCadastro": {},
            "itensRecebimento": [],
        }
        with mock.patch.object(pipeline, "paginar", return_value=[nf_stub]), \
             mock.patch.object(pipeline, "omie_request", return_value=rec_stub):
            _, itens, _ = pipeline.coletar_documentos_fiscais("2026-04-01", "2026-04-30")

        self.assertEqual(len(itens), 3)
        self.assertEqual([it["n_sequencia"] for it in itens], [1, 2, 3])

    def test_coletar_enriquecimento_join_por_n_cod_item(self):
        """Join nfProdInt.nCodItem = itensCabec.nIdItem deve preencher c_descricao_produto."""
        import unittest.mock as mock

        nf_stub = {
            "compl": {"nIdReceb": 42},
            "ide": {"tpNF": 0, "dEmi": "15/04/2026"},
            "nfDestInt": {}, "nfEmitInt": {},
            "det": [{
                "nfProdInt": {"nCodItem": 8962737091},
                "prod": {"xProd": "Fechadura", "NCM": "83018090", "CFOP": "2102"},
            }],
            "titulos": [],
        }
        rec_stub = {
            "cabec": {}, "totais": {}, "infoCadastro": {},
            "itensRecebimento": [{
                "itensCabec": {
                    "nIdItem": 8962737091,
                    "nSequencia": 1,
                    "cDescricaoProduto": "FECHADURA INOX 304",
                    "nIdProduto": 555,
                    "cIgnorarItem": "N",
                },
            }],
        }
        with mock.patch.object(pipeline, "paginar", return_value=[nf_stub]), \
             mock.patch.object(pipeline, "omie_request", return_value=rec_stub):
            _, itens, _ = pipeline.coletar_documentos_fiscais("2026-04-01", "2026-04-30")

        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0]["c_descricao_produto"], "FECHADURA INOX 304")
        self.assertEqual(itens[0]["n_id_produto"], 555)
        self.assertEqual(itens[0]["n_sequencia"], 1)

    # ------------------------------------------------------------------
    # _normalizar_documento_fiscal — destinatário vs. emitente externo
    # ------------------------------------------------------------------

    def test_documento_destinatario_e_emitente(self):
        """
        Koti é a destinatária (nfDestInt / recebimentonfe.cabec).
        Fornecedor externo é nfEmitInt — apenas nCodEmp, sem CNPJ direto.
        """
        nf_obj = {
            "compl": {"nIdReceb": 55555, "cChaveNFe": "CHAVE_MASCARADA", "nIdNF": 9001},
            "ide": {"tpNF": 0, "dEmi": "15/04/2026", "dSaiEnt": "16/04/2026"},
            "nfDestInt": {"nCodCli": 100, "cRazao": "STUDIO KOTI LTDA", "cnpj_cpf": "00.000.000/0001-00"},
            "nfEmitInt": {"nCodEmp": 200},
        }
        rec_obj = {
            "cabec": {
                "nIdFornecedor": 100,
                "cRazaoSocial": "STUDIO KOTI LTDA",
                "cCNPJ_CPF": "00.000.000/0001-00",
                "nValorNFe": 3200.0,
                "cEtapa": "60",
                "cNaturezaOperacao": "COMPRA DE MERCADORIA",
            },
            "totais": {"vTotalProdutos": 3000.0, "vFrete": 200.0},
            "infoCadastro": {"cFaturado": "S", "cRecebido": "S", "cCancelada": "N"},
        }
        row = pipeline._normalizar_documento_fiscal(nf_obj, rec_obj, "2026-05-17T00:00:00")

        # Destinatário = Koti (campo vem de recebimentonfe.cabec ou nfDestInt)
        self.assertEqual(row["n_id_destinatario"], 100)
        self.assertIn("KOTI", row["razao_social_destinatario"].upper())
        self.assertEqual(row["cnpj_destinatario"], "00.000.000/0001-00")

        # Emitente externo = apenas código Omie interno
        self.assertEqual(row["n_emit_cod_emp"], 200)

        # Campos que NÃO devem usar o nome "fornecedor" em seus valores
        self.assertNotIn("fornecedor", str(row.get("razao_social_destinatario", "")).lower())

        # Totais e estado
        self.assertEqual(row["valor_total_nfe"], 3200.0)
        self.assertEqual(row["etapa"], "60")
        self.assertEqual(row["recebido"], "S")
        self.assertEqual(row["cancelada"], "N")
        self.assertEqual(row["origem_dado"], "nfconsultar+recebimentonfe")

    def test_documento_sem_rec_obj(self):
        """Sem recebimento, campos vêm de nfDestInt/ide."""
        nf_obj = {
            "compl": {"nIdReceb": 11111},
            "ide": {"tpNF": 0, "dEmi": "01/03/2026"},
            "nfDestInt": {"nCodCli": 50, "cRazao": "KOTI", "cnpj_cpf": "XX"},
            "nfEmitInt": {"nCodEmp": 99},
        }
        row = pipeline._normalizar_documento_fiscal(nf_obj, {}, "now")
        self.assertEqual(row["n_id_receb"], 11111)
        self.assertEqual(row["n_id_destinatario"], 50)
        self.assertEqual(row["n_emit_cod_emp"], 99)
        self.assertIsNone(row["etapa"])

    def test_documento_data_emissao_parse(self):
        nf_obj = {
            "compl": {},
            "ide": {"dEmi": "22/02/2026"},
            "nfDestInt": {}, "nfEmitInt": {},
        }
        rec_obj = {"cabec": {"dEmissaoNFe": "22/02/2026"}, "totais": {}, "infoCadastro": {}}
        row = pipeline._normalizar_documento_fiscal(nf_obj, rec_obj, "now")
        self.assertEqual(row["data_emissao"], "2026-02-22")

    # ------------------------------------------------------------------
    # coletar_documentos_fiscais — dry-run sem API (lista vazia)
    # ------------------------------------------------------------------

    def test_coletar_lista_vazia(self):
        """Com lista de NFs vazia, retorna 3 listas vazias sem erros."""
        import unittest.mock as mock
        with mock.patch.object(pipeline, "paginar", return_value=[]):
            docs, itens, titulos = pipeline.coletar_documentos_fiscais("2026-01-01", "2026-01-31")
        self.assertEqual(docs, [])
        self.assertEqual(itens, [])
        self.assertEqual(titulos, [])

    def test_coletar_limite_respeitado(self):
        """O parâmetro `limite` trunca a lista de NFs (IDs distintos para não interferir na dedup)."""
        import unittest.mock as mock
        nf_stubs = [
            {"compl": {"nIdReceb": i}, "ide": {"tpNF": 0},
             "nfDestInt": {}, "nfEmitInt": {}, "det": [], "titulos": []}
            for i in range(1, 11)
        ]
        with mock.patch.object(pipeline, "paginar", return_value=nf_stubs), \
             mock.patch.object(pipeline, "omie_request", return_value={}):
            docs, itens, titulos = pipeline.coletar_documentos_fiscais(
                "2026-01-01", "2026-01-31", limite=3
            )
        self.assertEqual(len(docs), 3)

    # ------------------------------------------------------------------
    # coletar_documentos_fiscais — deduplicação de resposta API
    # ------------------------------------------------------------------

    def _nf_stub_com_id(self, n_id_receb, n_cod_titulo=None, n_parcela=1):
        """NF mínima com nIdReceb e um item/título opcionais."""
        tit = [{"nCodTitulo": n_cod_titulo, "nParcela": n_parcela}] if n_cod_titulo else []
        return {
            "compl": {"nIdReceb": n_id_receb},
            "ide": {"tpNF": 0, "dEmi": "01/08/2025"},
            "nfDestInt": {}, "nfEmitInt": {},
            "det": [{"prod": {"CFOP": "1102", "NCM": ""}, "nfProdInt": {"nCodItem": 1}}],
            "titulos": tit,
        }

    def test_coletar_dedup_docs_duplicados(self):
        """API retorna o mesmo nIdReceb duas vezes — coletar deve retornar apenas 1 doc."""
        import unittest.mock as mock
        nf = self._nf_stub_com_id(999)
        with mock.patch.object(pipeline, "paginar", return_value=[nf, nf]), \
             mock.patch.object(pipeline, "omie_request", return_value={}):
            docs, itens, _ = pipeline.coletar_documentos_fiscais("2025-08-01", "2025-08-31")
        self.assertEqual(len(docs), 1)
        self.assertEqual(len(itens), 1)

    def test_coletar_dedup_itens_duplicados(self):
        """Se normalização gerar dois itens com mesma chave (n_id_receb, n_sequencia), deve manter 1."""
        import unittest.mock as mock
        nf = self._nf_stub_com_id(777)
        # Dois itens com mesmo nCodItem → mesmo seq=1 positional, mesma chave
        nf2 = dict(nf)
        nf2["det"] = nf["det"] + nf["det"]  # dois itens idênticos → seq 1 e 2 (diferentes)
        # Para duplicar (n_id_receb, n_sequencia), precisamos do mesmo nf duas vezes
        with mock.patch.object(pipeline, "paginar", return_value=[nf, nf]), \
             mock.patch.object(pipeline, "omie_request", return_value={}):
            _, itens, _ = pipeline.coletar_documentos_fiscais("2025-08-01", "2025-08-31")
        # doc deduplicado → 1 doc → 1 item (seq=1); sem duplicata de item
        seen = set()
        for it in itens:
            key = (it["n_id_receb"], it["n_sequencia"])
            self.assertNotIn(key, seen, f"item duplicado: {key}")
            seen.add(key)

    def test_coletar_dedup_titulos_duplicados(self):
        """API retorna o mesmo (n_id_receb, n_cod_titulo, n_parcela) duas vezes — coletar deve manter 1."""
        import unittest.mock as mock
        nf = self._nf_stub_com_id(888, n_cod_titulo=42, n_parcela=1)
        with mock.patch.object(pipeline, "paginar", return_value=[nf, nf]), \
             mock.patch.object(pipeline, "omie_request", return_value={}):
            _, _, titulos = pipeline.coletar_documentos_fiscais("2025-08-01", "2025-08-31")
        self.assertEqual(len(titulos), 1)

    def test_coletar_dedup_titulos_parcelas_distintas_preservadas(self):
        """Mesmo nCodTitulo com n_parcela diferente = títulos distintos, ambos preservados."""
        import unittest.mock as mock
        nf_com_dois_titulos = {
            "compl": {"nIdReceb": 555},
            "ide": {"tpNF": 0, "dEmi": "01/03/2026"},
            "nfDestInt": {}, "nfEmitInt": {},
            "det": [],
            "titulos": [
                {"nCodTitulo": 77, "nParcela": 1},
                {"nCodTitulo": 77, "nParcela": 2},
            ],
        }
        with mock.patch.object(pipeline, "paginar", return_value=[nf_com_dois_titulos]), \
             mock.patch.object(pipeline, "omie_request", return_value={}):
            _, _, titulos = pipeline.coletar_documentos_fiscais("2026-03-01", "2026-03-31")
        self.assertEqual(len(titulos), 2, "parcelas distintas não devem ser deduplicadas")
        parcelas = {t["n_parcela"] for t in titulos}
        self.assertEqual(parcelas, {1, 2})

    def test_coletar_sem_duplicados_nao_altera_resultado(self):
        """Sem duplicados, dedup não modifica os dados."""
        import unittest.mock as mock
        nfs = [self._nf_stub_com_id(i) for i in (1, 2, 3)]
        with mock.patch.object(pipeline, "paginar", return_value=nfs), \
             mock.patch.object(pipeline, "omie_request", return_value={}):
            docs, itens, _ = pipeline.coletar_documentos_fiscais("2025-08-01", "2025-08-31")
        self.assertEqual(len(docs), 3)
        self.assertEqual(len(itens), 3)

    # ------------------------------------------------------------------
    # sincronizar_documentos_fiscais — Fase 9: MERGE idempotente itens
    # ------------------------------------------------------------------

    def test_sincronizar_aborta_se_n_sequencia_null(self):
        """Se qualquer item tiver n_sequencia=None, deve levantar ValueError antes de tocar no BQ."""
        import unittest.mock as mock
        itens_com_null = [
            {"n_id_receb": 1, "n_sequencia": 1},
            {"n_id_receb": 1, "n_sequencia": None},
        ]
        with mock.patch.object(pipeline, "coletar_documentos_fiscais",
                               return_value=([], itens_com_null, [])), \
             mock.patch.object(pipeline, "merge_to_bq") as mock_merge:
            with self.assertRaises(ValueError) as ctx:
                pipeline.sincronizar_documentos_fiscais(mock.MagicMock(), "2026-01-01", "2026-01-31")
        self.assertIn("n_sequencia NULL", str(ctx.exception))
        mock_merge.assert_not_called()

    def test_sincronizar_itens_usa_merge_com_chave_composta(self):
        """sincronizar_documentos_fiscais deve chamar merge_to_bq com chave ["n_id_receb", "n_sequencia"] e no_delete=True."""
        import unittest.mock as mock
        itens_ok = [{"n_id_receb": 99, "n_sequencia": 1}]
        with mock.patch.object(pipeline, "coletar_documentos_fiscais",
                               return_value=([], itens_ok, [])), \
             mock.patch.object(pipeline, "merge_to_bq", return_value={}) as mock_merge, \
             mock.patch.object(pipeline, "salvar_checkpoint"):
            pipeline.sincronizar_documentos_fiscais(mock.MagicMock(), "2026-04-18", "2026-05-18")
        calls = mock_merge.call_args_list
        itens_call = next(c for c in calls if c.args[1] == "documentos_fiscais_itens")
        self.assertEqual(itens_call.args[3], ["n_id_receb", "n_sequencia"])
        self.assertTrue(itens_call.kwargs.get("no_delete"))

    def test_sincronizar_titulos_usa_chave_composta(self):
        """sincronizar_documentos_fiscais deve chamar merge_to_bq com chave
        ["n_id_receb", "n_cod_titulo", "n_parcela"] para documentos_fiscais_titulos."""
        import unittest.mock as mock
        itens_ok = [{"n_id_receb": 1, "n_sequencia": 1}]
        titulo_ok = [{"n_id_receb": 1, "n_cod_titulo": 100, "n_parcela": 1}]
        with mock.patch.object(pipeline, "coletar_documentos_fiscais",
                               return_value=([{}], itens_ok, titulo_ok)), \
             mock.patch.object(pipeline, "merge_to_bq", return_value={}) as mock_merge, \
             mock.patch.object(pipeline, "salvar_checkpoint"):
            pipeline.sincronizar_documentos_fiscais(mock.MagicMock(), "2026-03-01", "2026-03-31")
        calls = mock_merge.call_args_list
        tit_call = next(c for c in calls if c.args[1] == "documentos_fiscais_titulos")
        self.assertEqual(
            tit_call.args[3], ["n_id_receb", "n_cod_titulo", "n_parcela"],
            "chave composta deve ser (n_id_receb, n_cod_titulo, n_parcela)"
        )
        self.assertTrue(tit_call.kwargs.get("no_delete"))

    def test_sincronizar_fiscais_todas_as_tabelas_usam_no_delete(self):
        """Todas as 3 tabelas fiscais devem usar no_delete=True — nunca DELETE automático."""
        import unittest.mock as mock
        itens_ok = [{"n_id_receb": 1, "n_sequencia": 1}]
        with mock.patch.object(pipeline, "coletar_documentos_fiscais",
                               return_value=([{}], itens_ok, [{}])), \
             mock.patch.object(pipeline, "merge_to_bq", return_value={}) as mock_merge, \
             mock.patch.object(pipeline, "salvar_checkpoint"):
            pipeline.sincronizar_documentos_fiscais(mock.MagicMock(), "2026-01-01", "2026-01-31")
        for table in ["documentos_fiscais", "documentos_fiscais_itens", "documentos_fiscais_titulos"]:
            call = next(c for c in mock_merge.call_args_list if c.args[1] == table)
            self.assertTrue(call.kwargs.get("no_delete"), f"{table} deve usar no_delete=True")

    def test_merge_to_bq_no_delete_omite_clausula_delete(self):
        """merge_to_bq com no_delete=True não deve incluir WHEN NOT MATCHED BY SOURCE THEN DELETE."""
        import unittest.mock as mock
        captured_sql = []

        def fake_query(sql, *a, **kw):
            captured_sql.append(sql)
            m = mock.MagicMock()
            m.result.return_value = [mock.MagicMock(c=0)]
            m.num_dml_affected_rows = 0
            return m

        mock_client = mock.MagicMock()
        mock_client.query.side_effect = fake_query
        mock_client.get_table.return_value.schema = []
        load_job = mock.MagicMock()
        load_job.result.return_value = None
        mock_client.load_table_from_json.return_value = load_job

        pipeline.merge_to_bq(
            mock_client, "test_table", [{"id": 1, "val": "x"}],
            "id", ["val"], ["id", "val"],
            no_delete=True,
        )
        merge_sql = next(s for s in captured_sql if "MERGE" in s)
        self.assertNotIn("NOT MATCHED BY SOURCE", merge_sql)


if __name__ == "__main__":
    unittest.main()
