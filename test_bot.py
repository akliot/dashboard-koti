#!/usr/bin/env python3
"""
Stress test do bot financeiro — simula o dono do Studio Koti fazendo perguntas reais.
Roda todas as queries via FinancialAssistant (sem Telegram), valida respostas.

Uso:
  GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-key.json \
  GEMINI_API_KEY=AIzaSyDwvfWYo_1bwDens98tHJXgJkMhGYT12fc \
  python3 test_bot.py
"""

import asyncio
import os
import sys
import json

os.environ.setdefault("GCP_PROJECT_ID", "dashboard-koti-omie")
os.environ.setdefault("BQ_DATASET", "studio_koti")

from bot_telegram import FinancialAssistant, GeminiProvider, chat_history
from google.cloud import bigquery

# Simula conversas reais de um dono de empresa de marcenaria de alto padrão
# Cada teste: pergunta, critérios, se é follow-up (usa histórico)
TESTS = [
    # ========== CENÁRIO 1: Revisão matinal do caixa ==========
    {"q": "Bom dia, qual o saldo de todas as contas?", "must_contain": ["R$"], "must_not_contain": ["não encontrei"], "reset": True},
    {"q": "Quais contas a pagar vencem essa semana?", "must_contain": ["R$"], "must_not_contain": ["não encontrei", "similares"]},
    {"q": "E a receber?", "must_contain": ["R$"], "must_not_contain": ["não encontrei", "similares"]},

    # ========== CENÁRIO 2: Análise de fornecedor ==========
    {"q": "Quanto eu gastei esse ano de kairos?", "must_contain": ["R$"], "must_not_contain": ["R$ 0", "não encontrei"], "reset": True},
    {"q": "E de castini?", "must_contain": ["R$"], "must_not_contain": ["não encontrei"]},
    {"q": "Quais os maiores fornecedores por gasto em 2026?", "must_contain": ["R$"], "must_not_contain": ["não encontrei"]},

    # ========== CENÁRIO 3: Acompanhamento de projeto ==========
    {"q": "Quanto eu gastei até o momento no projeto nex one 1513?", "must_contain": ["R$"], "must_not_contain": ["R$ 0", "não encontrei"], "reset": True},
    {"q": "E no hub brooklin 1305?", "must_contain": ["R$"], "must_not_contain": ["não encontrei"]},
    {"q": "Qual a margem desse projeto?", "must_contain": ["R$"], "must_not_contain": ["não encontrei"]},
    {"q": "Quais projetos tiveram mais gastos que receitas?", "must_contain": ["R$"], "must_not_contain": ["não encontrei", "similares"]},

    # ========== CENÁRIO 4: Faturamento e recebimentos ==========
    {"q": "Quanto eu faturei esse mês?", "must_contain": ["R$"], "must_not_contain": ["não encontrei", "R$ 0"], "reset": True},
    {"q": "Quais são os recebimentos previstos de março?", "must_contain": ["R$"], "must_not_contain": ["não encontrei"]},
    {"q": "Quanto eu já recebi desse valor?", "must_contain": ["R$"], "must_not_contain": ["2.298"]},
    {"q": "Quais clientes pagaram esse mês?", "must_contain": ["R$"], "must_not_contain": ["não encontrei", "similares"]},
    {"q": "Qual cliente mais me deve?", "must_contain": ["R$"], "must_not_contain": ["não encontrei"]},

    # ========== CENÁRIO 5: Análise de categorias ==========
    {"q": "Quais categorias de despesa tiveram mais gastos esse ano?", "must_contain": ["R$"], "must_not_contain": ["não encontrei"], "reset": True},
    {"q": "Quanto gastamos de marcenaria em março?", "must_contain": ["R$"], "must_not_contain": ["não encontrei"]},
    {"q": "E de mão de obra?", "must_contain": ["R$"], "must_not_contain": ["não encontrei", "similares"]},

    # ========== CENÁRIO 6: Comparações temporais ==========
    {"q": "Compare meu faturamento de fevereiro vs março", "must_contain": ["R$"], "must_not_contain": ["não encontrei"], "reset": True},
    {"q": "Quais projetos geraram receita em fevereiro?", "must_contain": ["R$"], "must_not_contain": ["não encontrei", "2025"]},

    # ========== CENÁRIO 7: Perguntas rápidas do dia a dia ==========
    {"q": "Tem alguma conta vencida hoje?", "must_not_contain": ["similares", "TREVISAN"], "reset": True},
    {"q": "Quais notas fiscais foram emitidas essa semana?", "must_not_contain": ["similares"]},
    {"q": "Total de despesas fixas esse mês", "must_not_contain": ["similares"]},
    {"q": "Quero ver os lançamentos da alpha1", "must_contain": ["R$"], "must_not_contain": ["não encontrei"]},

    # ========== CENÁRIO 8: Perguntas vagas (bot deve lidar bem) ==========
    {"q": "Recebimentos", "must_not_contain": ["similares", "RECEITA FEDERAL"], "reset": True},
    {"q": "Pagamentos de março", "must_contain": ["R$"], "must_not_contain": ["similares"]},
    {"q": "Projetos", "must_not_contain": ["similares"]},
    {"q": "Quero uma relação dos projetos que tem data de emissão de março e quero saber o valor total", "must_contain": ["R$"], "must_not_contain": ["similares", "não encontrei"]},

    # ========== CENÁRIO 9: Perguntas com contexto implícito ==========
    {"q": "Quanto gastamos de norte sul em março?", "must_contain": ["R$"], "must_not_contain": ["não encontrei"], "reset": True},
    {"q": "Mostra os detalhes", "must_contain": ["R$"], "must_not_contain": ["não encontrei"]},
    {"q": "Tem algo a pagar deles ainda?", "must_not_contain": ["similares"]},

    # ========== CENÁRIO 10: Edge cases ==========
    {"q": "btg", "must_contain": ["R$"], "must_not_contain": ["similares"], "reset": True},
    {"q": "Qual foi o último sync?", "must_not_contain": ["não encontrei"]},
]


async def run_tests():
    llm = GeminiProvider()
    bq_client = bigquery.Client(project="dashboard-koti-omie")
    assistant = FinancialAssistant(llm, bq_client)

    passed = 0
    failed = 0
    failures = []
    history = []

    for i, test in enumerate(TESTS, 1):
        q = test["q"]

        # Reset de contexto para novo cenário
        if test.get("reset"):
            history = []
            print(f"\n{'='*60}")

        print(f"[{i}/{len(TESTS)}] {q}")

        try:
            response = await assistant.process_message(q, history)
        except Exception as e:
            response = f"ERRO: {e}"

        # Salvar no histórico
        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": response[:500]})
        history = history[-10:]

        # Validar
        ok = True
        issues = []
        for mc in test.get("must_contain", []):
            if mc.lower() not in response.lower():
                ok = False
                issues.append(f"FALTA: '{mc}'")
        for mnc in test.get("must_not_contain", []):
            if mnc.lower() in response.lower():
                ok = False
                issues.append(f"INDESEJADO: '{mnc}'")

        if ok:
            passed += 1
            print(f"  ✅ {response[:120]}...")
        else:
            failed += 1
            failures.append({"q": q, "response": response[:400], "issues": issues})
            print(f"  ❌ {', '.join(issues)}")
            print(f"     Resp: {response[:200]}...")

        # Rate limit: pausa entre chamadas ao Gemini (free tier = 10 req/min)
        await asyncio.sleep(4)

    # Relatório
    pct = (passed / len(TESTS)) * 100
    print(f"\n{'='*60}")
    print(f"📊 RESULTADO: {passed}/{len(TESTS)} ({pct:.0f}%)")
    print(f"{'='*60}")

    if failures:
        print(f"\n❌ {len(failures)} FALHAS:")
        for f in failures:
            print(f"\n  Q: {f['q']}")
            print(f"  Issues: {', '.join(f['issues'])}")

    with open("/tmp/bot_test_results.json", "w") as fp:
        json.dump({"passed": passed, "failed": failed, "total": len(TESTS),
                   "pct": pct, "failures": failures}, fp, ensure_ascii=False, indent=2)
    print(f"\n💾 Salvo em /tmp/bot_test_results.json")


if __name__ == "__main__":
    asyncio.run(run_tests())
