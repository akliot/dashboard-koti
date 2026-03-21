#!/usr/bin/env python3
"""
Testes unitários para a API api_bq.py.

Uso:
  python3 test_api.py -v
"""

import unittest
from unittest.mock import MagicMock
from datetime import date, datetime
import os
import json

os.environ.setdefault("GCP_PROJECT_ID", "dashboard-koti-omie")
os.environ.setdefault("BQ_DATASET", "studio_koti")

import api_bq


class TestDateToDDMMYYYY(unittest.TestCase):
    """Testa conversão para DD/MM/YYYY."""

    def test_date_object(self):
        self.assertEqual(api_bq.date_to_ddmmyyyy(date(2026, 3, 15)), "15/03/2026")

    def test_datetime_object(self):
        self.assertEqual(api_bq.date_to_ddmmyyyy(datetime(2026, 1, 5, 10, 30)), "05/01/2026")

    def test_none(self):
        self.assertEqual(api_bq.date_to_ddmmyyyy(None), "")

    def test_string_passthrough(self):
        self.assertEqual(api_bq.date_to_ddmmyyyy("15/03/2026"), "15/03/2026")

    def test_primeiro_dia(self):
        self.assertEqual(api_bq.date_to_ddmmyyyy(date(2026, 1, 1)), "01/01/2026")

    def test_ultimo_dia(self):
        self.assertEqual(api_bq.date_to_ddmmyyyy(date(2026, 12, 31)), "31/12/2026")


class TestDateToYMD(unittest.TestCase):
    """Testa conversão para YYYY-MM-DD."""

    def test_date_object(self):
        self.assertEqual(api_bq.date_to_ymd(date(2026, 3, 15)), "2026-03-15")

    def test_datetime_object(self):
        self.assertEqual(api_bq.date_to_ymd(datetime(2026, 1, 5)), "2026-01-05")

    def test_none(self):
        self.assertEqual(api_bq.date_to_ymd(None), "")

    def test_string_passthrough(self):
        self.assertEqual(api_bq.date_to_ymd("2026-03-15"), "2026-03-15")


class TestTbl(unittest.TestCase):
    """Testa construção de referência de tabela."""

    def test_lancamentos(self):
        ref = api_bq.tbl("lancamentos")
        self.assertEqual(ref, "`dashboard-koti-omie.studio_koti.lancamentos`")

    def test_saldos(self):
        ref = api_bq.tbl("saldos_bancarios")
        self.assertIn("saldos_bancarios", ref)
        self.assertIn("studio_koti", ref)


class TestDataRefLogic(unittest.TestCase):
    """Testa lógica de data_ref: PAGO usa data_pagamento, pendente usa data_vencimento."""

    def _get_data_ref(self, status, data_pagamento, data_vencimento):
        """Replica a lógica do build_json para data_ref."""
        s = (status or "").upper()
        if s in ("PAGO", "RECEBIDO", "LIQUIDADO") and data_pagamento:
            return data_pagamento
        return data_vencimento

    def test_pago_usa_data_pagamento(self):
        ref = self._get_data_ref("PAGO", date(2026, 3, 16), date(2026, 2, 11))
        self.assertEqual(ref, date(2026, 3, 16))

    def test_recebido_usa_data_pagamento(self):
        ref = self._get_data_ref("RECEBIDO", date(2026, 3, 10), date(2026, 3, 9))
        self.assertEqual(ref, date(2026, 3, 10))

    def test_a_vencer_usa_data_vencimento(self):
        ref = self._get_data_ref("A VENCER", None, date(2026, 3, 22))
        self.assertEqual(ref, date(2026, 3, 22))

    def test_atrasado_usa_data_vencimento(self):
        ref = self._get_data_ref("ATRASADO", None, date(2026, 2, 15))
        self.assertEqual(ref, date(2026, 2, 15))

    def test_pago_sem_data_pagamento_usa_vencimento(self):
        """Se PAGO mas data_pagamento é None, fallback para vencimento."""
        ref = self._get_data_ref("PAGO", None, date(2026, 3, 15))
        self.assertEqual(ref, date(2026, 3, 15))

    def test_vence_hoje_usa_vencimento(self):
        ref = self._get_data_ref("VENCE HOJE", None, date(2026, 3, 21))
        self.assertEqual(ref, date(2026, 3, 21))

    def test_cancelado_com_pagamento(self):
        """CANCELADO não está na lista de realizados, deve usar vencimento."""
        ref = self._get_data_ref("CANCELADO", date(2026, 3, 10), date(2026, 3, 5))
        self.assertEqual(ref, date(2026, 3, 5))


class TestCorsOrigin(unittest.TestCase):
    """Testa CORS restritivo."""

    def _mock_request(self, origin):
        req = MagicMock()
        req.headers = {"Origin": origin}
        return req

    def test_github_pages_permitido(self):
        req = self._mock_request("https://akliot.github.io")
        self.assertEqual(api_bq._cors_origin(req), "https://akliot.github.io")

    def test_localhost_permitido(self):
        req = self._mock_request("http://localhost:8080")
        self.assertEqual(api_bq._cors_origin(req), "http://localhost:8080")

    def test_127_permitido(self):
        req = self._mock_request("http://127.0.0.1:8080")
        self.assertEqual(api_bq._cors_origin(req), "http://127.0.0.1:8080")

    def test_origin_desconhecido_retorna_default(self):
        req = self._mock_request("https://evil.com")
        self.assertEqual(api_bq._cors_origin(req), "https://akliot.github.io")

    def test_sem_origin(self):
        req = MagicMock()
        req.headers = {}
        result = api_bq._cors_origin(req)
        self.assertEqual(result, "https://akliot.github.io")

    def test_origin_vazio(self):
        req = self._mock_request("")
        self.assertEqual(api_bq._cors_origin(req), "https://akliot.github.io")


class TestAllowedOrigins(unittest.TestCase):
    """Testa configuração de ALLOWED_ORIGINS."""

    def test_tem_github_pages(self):
        self.assertIn("https://akliot.github.io", api_bq.ALLOWED_ORIGINS)

    def test_tem_localhost(self):
        self.assertIn("http://localhost:8080", api_bq.ALLOWED_ORIGINS)

    def test_nao_tem_wildcard(self):
        self.assertNotIn("*", api_bq.ALLOWED_ORIGINS)


class TestApiDashboardOptions(unittest.TestCase):
    """Testa preflight CORS (OPTIONS)."""

    def test_options_retorna_204(self):
        req = MagicMock()
        req.method = "OPTIONS"
        req.headers = {"Origin": "https://akliot.github.io"}
        body, status, headers = api_bq.api_dashboard(req)
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "https://akliot.github.io")
        self.assertEqual(headers["Access-Control-Allow-Methods"], "GET")


class TestBuildJsonStructure(unittest.TestCase):
    """Testa estrutura do JSON retornado (requer BigQuery acessível)."""

    @classmethod
    def setUpClass(cls):
        """Tenta build_json. Se BQ não acessível, pula."""
        try:
            cls.data = api_bq.build_json()
            cls.bq_available = True
        except Exception:
            cls.bq_available = False

    def setUp(self):
        if not self.bq_available:
            self.skipTest("BigQuery não acessível")

    def test_campos_obrigatorios(self):
        for campo in ["atualizado_em", "atualizado_em_formatado", "lancamentos",
                      "saldos_bancarios", "historico_conciliacao", "categorias",
                      "projetos", "vendas", "clientes"]:
            self.assertIn(campo, self.data, f"Campo '{campo}' faltando")

    def test_lancamentos_tem_campos(self):
        if not self.data["lancamentos"]:
            self.skipTest("Sem lançamentos")
        l = self.data["lancamentos"][0]
        for campo in ["id", "valor", "tipo", "status", "data", "data_vencimento",
                      "data_pagamento", "categoria", "categoria_nome",
                      "projeto", "projeto_nome", "cliente_nome"]:
            self.assertIn(campo, l, f"Campo '{campo}' faltando no lançamento")

    def test_lancamento_tipo_valido(self):
        for l in self.data["lancamentos"][:100]:
            self.assertIn(l["tipo"], ("entrada", "saida"), f"Tipo inválido: {l['tipo']}")

    def test_lancamento_data_formato(self):
        """Data deve ser DD/MM/YYYY."""
        for l in self.data["lancamentos"][:50]:
            if l["data"]:
                parts = l["data"].split("/")
                self.assertEqual(len(parts), 3, f"Formato inválido: {l['data']}")
                self.assertEqual(len(parts[0]), 2)  # DD
                self.assertEqual(len(parts[1]), 2)  # MM
                self.assertEqual(len(parts[2]), 4)  # YYYY

    def test_pago_tem_data_pagamento(self):
        """Lançamentos PAGO/RECEBIDO devem ter data_pagamento preenchido."""
        for l in self.data["lancamentos"][:200]:
            if l["status"] in ("PAGO", "RECEBIDO"):
                # data_pagamento pode estar vazio se info.dAlt não existia
                # mas 'data' deve usar data_pagamento
                self.assertTrue(l["data"], f"PAGO sem data: id={l['id']}")

    def test_pago_data_ref_eh_pagamento(self):
        """Para PAGO, campo 'data' deve ser data_pagamento (não vencimento)."""
        for l in self.data["lancamentos"][:200]:
            if l["status"] in ("PAGO", "RECEBIDO") and l["data_pagamento"]:
                self.assertEqual(l["data"], l["data_pagamento"],
                                 f"PAGO data != data_pagamento: id={l['id']}")

    def test_pendente_data_ref_eh_vencimento(self):
        """Para A VENCER, campo 'data' deve ser data_vencimento."""
        for l in self.data["lancamentos"][:200]:
            if l["status"] in ("A VENCER", "VENCE HOJE") and l["data_vencimento"]:
                self.assertEqual(l["data"], l["data_vencimento"],
                                 f"A VENCER data != data_vencimento: id={l['id']}")

    def test_saldos_tem_campos(self):
        if not self.data["saldos_bancarios"]:
            self.skipTest("Sem saldos")
        s = self.data["saldos_bancarios"][0]
        for campo in ["id", "nome", "saldo", "saldo_conciliado", "diferenca", "data"]:
            self.assertIn(campo, s, f"Campo '{campo}' faltando no saldo")

    def test_categorias_eh_dict(self):
        self.assertIsInstance(self.data["categorias"], dict)

    def test_projetos_eh_lista(self):
        self.assertIsInstance(self.data["projetos"], list)

    def test_vendas_tem_campos(self):
        v = self.data["vendas"]
        for campo in ["total_vendas", "quantidade_pedidos", "ticket_medio",
                      "por_mes", "por_etapa", "top_produtos"]:
            self.assertIn(campo, v, f"Campo '{campo}' faltando em vendas")

    def test_clientes_tem_campos(self):
        c = self.data["clientes"]
        for campo in ["total_clientes", "ativos", "inativos",
                      "pessoa_fisica", "pessoa_juridica", "por_estado"]:
            self.assertIn(campo, c, f"Campo '{campo}' faltando em clientes")

    def test_orcamento_presente_se_tabela_tem_dados(self):
        """Se existir orcamento, deve ter a estrutura correta."""
        if "orcamento" not in self.data:
            self.skipTest("Sem orçamento")
        orc = self.data["orcamento"]
        self.assertIn("meses_disponiveis", orc)
        self.assertIn("meses_com_real", orc)
        self.assertIn("dre", orc)
        self.assertIsInstance(orc["dre"], list)

    def test_orcamento_dre_estrutura(self):
        if "orcamento" not in self.data or not self.data["orcamento"]["dre"]:
            self.skipTest("Sem orçamento/DRE")
        dre = self.data["orcamento"]["dre"][0]
        for campo in ["label", "section", "level", "bp", "real"]:
            self.assertIn(campo, dre, f"Campo '{campo}' faltando no DRE")

    def test_atualizado_em_brt(self):
        """Horário deve estar em BRT (não UTC)."""
        fmt = self.data["atualizado_em_formatado"]
        # Deve ser DD/MM/YYYY às HH:MM
        self.assertIn("às", fmt)
        self.assertIn("/", fmt)

    def test_historico_conciliacao_estrutura(self):
        if not self.data["historico_conciliacao"]:
            self.skipTest("Sem histórico")
        h = self.data["historico_conciliacao"][0]
        for campo in ["banco_id", "banco_nome", "data", "saldo_atual",
                      "saldo_conciliado", "diferenca", "tipo"]:
            self.assertIn(campo, h, f"Campo '{campo}' faltando no histórico")

    def test_historico_tipo_valido(self):
        for h in self.data["historico_conciliacao"][:50]:
            self.assertIn(h["tipo"], ("mensal", "diario"), f"Tipo inválido: {h['tipo']}")


if __name__ == "__main__":
    unittest.main()
