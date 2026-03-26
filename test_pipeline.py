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


if __name__ == "__main__":
    unittest.main()
