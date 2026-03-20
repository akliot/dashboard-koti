#!/usr/bin/env python3
"""
Bot Telegram Financeiro — Studio Koti
Responde perguntas financeiras em linguagem natural, consultando BigQuery via Gemini.

Uso:
  python bot_telegram.py --local     # Polling (dev)
  python bot_telegram.py --cli       # CLI interativo (sem Telegram)

Variáveis de ambiente:
  TELEGRAM_BOT_TOKEN               — Token do bot Telegram
  GEMINI_API_KEY                   — API key do Google AI Studio
  GCP_PROJECT_ID                   — Projeto GCP (default: dashboard-koti-omie)
  BQ_DATASET                       — Dataset BigQuery (default: studio_koti)
  GOOGLE_APPLICATION_CREDENTIALS   — Path para service account JSON
  AUTHORIZED_CHAT_IDS              — IDs autorizados (comma-separated, opcional)
"""

import os
import sys
import asyncio
import logging
from datetime import date, datetime, timezone, timedelta

from google.cloud import bigquery

# ============================================================
# CONFIG
# ============================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "dashboard-koti-omie")
BQ_DATASET = os.environ.get("BQ_DATASET", "studio_koti")
BRT = timezone(timedelta(hours=-3))

# IDs autorizados (vazio = todos podem usar)
_auth_ids = os.environ.get("AUTHORIZED_CHAT_IDS", "")
AUTHORIZED_CHAT_IDS = set(int(x.strip()) for x in _auth_ids.split(",") if x.strip()) if _auth_ids else set()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# Memória de conversa por chat
chat_history: dict[int, list[dict]] = {}
MAX_HISTORY = 5


# ============================================================
# SCHEMA CONTEXT (descrição das tabelas para o LLM)
# ============================================================

def get_schema_context() -> str:
    return f"""Tabelas no dataset `{GCP_PROJECT_ID}.{BQ_DATASET}`:

1. lancamentos: id INT64, tipo STRING (entrada/saida), valor FLOAT64, status STRING (PAGO/RECEBIDO/A VENCER/ATRASADO/VENCE HOJE/CANCELADO), data_vencimento DATE, data_emissao DATE, data_pagamento DATE (data real do pagamento/recebimento, NULL se pendente), numero_documento STRING, categoria_codigo STRING, categoria_nome STRING, categoria_grupo STRING, projeto_id INT64, projeto_nome STRING, cliente_id INT64, cliente_nome STRING, is_faturamento_direto BOOL

2. saldos_bancarios: conta_id INT64, conta_nome STRING, conta_tipo STRING, saldo FLOAT64, saldo_conciliado FLOAT64, diferenca FLOAT64, data_referencia DATE

3. categorias: codigo STRING, nome STRING, grupo STRING

4. projetos: id INT64, nome STRING

5. clientes: id INT64, nome_fantasia STRING, razao_social STRING, estado STRING, ativo BOOL, pessoa_fisica BOOL

6. vendas_pedidos: pedido_id INT64, valor_mercadorias FLOAT64, etapa STRING, produto_descricao STRING, produto_quantidade FLOAT64

7. orcamento_dre: label STRING, section STRING, level INT64, mes STRING, valor_real FLOAT64, valor_bp FLOAT64, variacao_pct FLOAT64, mes_com_real BOOL

8. sync_log: sync_id STRING, status STRING, started_at TIMESTAMP, finished_at TIMESTAMP, duration_seconds INT64

Notas:
- tipo 'entrada' = receita, 'saida' = despesa
- Status PAGO = saída liquidada, RECEBIDO = entrada liquidada
- A data de hoje é {date.today().isoformat()}
- Sempre usar backticks para referências de tabela: `{GCP_PROJECT_ID}.{BQ_DATASET}.nome_tabela`
"""


# ============================================================
# LLM PROVIDER (abstração para trocar de IA depois)
# ============================================================

class LLMProvider:
    """Interface base para provedores de LLM."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError


class GeminiProvider(LLMProvider):
    """Provedor Google Gemini via google-genai SDK."""

    def __init__(self):
        from google import genai
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.model_name = "gemini-2.5-flash"
        log.info(f"Gemini provider inicializado ({self.model_name})")

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=f"{system_prompt}\n\n{user_prompt}",
        )
        return response.text


# ============================================================
# FINANCIAL ASSISTANT
# ============================================================

class FinancialAssistant:
    """Assistente financeiro que converte linguagem natural → SQL → resposta."""

    def __init__(self, llm: LLMProvider, bq_client: bigquery.Client):
        self.llm = llm
        self.bq = bq_client
        self.schema_context = get_schema_context()

    # Palavras genéricas do domínio financeiro — NÃO são nomes de empresa
    _STOPWORDS = {
        "quanto", "quero", "qual", "quais", "como", "para", "pagar", "paguei",
        "pagou", "pago", "receber", "recebi", "recebeu", "faturou", "faturamos",
        "faturei", "devo", "devemos", "total", "valor", "traga", "trazer",
        "mostrar", "mostra", "buscar", "listar", "lista", "preciso",
        "mês", "mes", "março", "marco", "fevereiro", "janeiro", "abril", "maio",
        "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
        "esse", "esta", "este", "nesse", "neste", "dessa", "desse", "ontem", "hoje",
        "semana", "contas", "conta", "saldo", "projeto", "projetos", "cliente",
        "clientes", "fornecedor", "fornecedores", "todos", "todas", "mais", "menos",
        "entre", "sobre", "lançamentos", "lancamentos", "lançamento", "lancamento",
        "relação", "relacao", "emissão", "emissao", "data", "vencimento",
        "pagamento", "recebimento", "recebimentos", "entradas", "saídas", "saidas",
        "receita", "receitas", "despesa", "despesas", "custo", "custos",
        "nota", "notas", "fiscal", "banco", "bancos", "saldos",
        "categoria", "categorias", "grupo", "grupos", "margem",
        "previstos", "previsto", "previsão", "previsao", "pendente", "pendentes",
        "aberto", "abertos", "vencido", "vencidos", "atrasado", "atrasados",
        "quem", "são", "sao", "será", "sera", "pode", "poderia", "gostaria",
        "preciso", "sendo", "feito", "fazer", "quero", "uma", "relação",
        "quantidade", "valores", "listagem", "extrato", "balanço", "balanco",
        "faturamento", "informações", "informacoes", "dados", "últimos", "ultimos",
    }

    async def process_message(self, text: str, history: list[dict] = None) -> str:
        """Processa uma pergunta e retorna resposta formatada."""
        try:
            # Montar contexto de conversa
            history_context = ""
            if history:
                history_context = "Conversa anterior (use como contexto):\n" + "\n".join(
                    f"{'Usuário' if m['role']=='user' else 'Bot'}: {m['content']}"
                    for m in history[-6:]
                ) + "\n\n"

            # 1. Gerar SQL via LLM (SEM resolve_name — deixa o Gemini entender)
            sql = self.generate_sql(text, history_context)
            log.info(f"SQL gerado: {sql[:200]}")

            # 2. Validar SQL (safety)
            if not self.is_safe_sql(sql):
                return "⚠️ Só posso executar consultas de leitura (SELECT)."

            # 3. Executar no BQ
            results = self.execute_query(sql)

            # 4. Se 0 resultados, decidir se faz fuzzy search ou não
            if not results or len(results) == 0:
                # Só buscar nomes similares se a pergunta tem NOME PRÓPRIO (não genérico)
                words = [w for w in text.lower().split()
                         if len(w) > 3 and w not in self._STOPWORDS]
                if words:
                    similar = self.find_similar_names(text)
                    if similar:
                        names_list = "\n".join(f"  • {n}" for n in similar[:5])
                        return f"🔍 Não encontrei resultados. Talvez você quis dizer:\n\n{names_list}\n\nTente novamente com o nome correto."
                return "🔍 Não encontrei dados para essa consulta. Tente reformular a pergunta com mais detalhes."

            # 5. Formatar resposta via LLM
            return self.format_response(text, sql, results)

        except Exception as e:
            log.error(f"Erro ao processar: {e}")
            return f"❌ Erro ao processar sua pergunta: {e}"

    def find_similar_names(self, question: str) -> list[str]:
        """Busca nomes similares. Só chamada quando há palavras não-genéricas."""
        words = [w for w in question.lower().split() if len(w) > 4 and w not in self._STOPWORDS]
        if not words:
            return []

        results = set()
        for word in words[:2]:  # Max 2 palavras
            pattern = f"%{word}%"
            try:
                q = f"""SELECT DISTINCT cliente_nome FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.lancamentos`
                        WHERE LOWER(cliente_nome) LIKE LOWER('{pattern}')
                        AND cliente_nome != '' LIMIT 5"""
                for row in self.bq.query(q).result(timeout=5):
                    results.add(row.cliente_nome)
            except Exception:
                pass
        return sorted(results)[:5]

    def generate_sql(self, question: str, history_context: str = "") -> str:
        """Converte pergunta em linguagem natural para SQL BigQuery."""
        hoje = date.today()
        mes_inicio = hoje.replace(day=1).isoformat()
        prox_mes = (hoje.replace(day=1) + timedelta(days=32)).replace(day=1).isoformat()

        prompt = f"""{history_context}Gere APENAS uma query SQL BigQuery para responder a pergunta abaixo.

Pergunta: "{question}"

{self.schema_context}

EXEMPLOS DE PERGUNTAS COMUNS → SQL (estamos em {hoje.year}, mês {hoje.month}):

"quanto faturei esse mês" ou "faturamento de março" →
SELECT ROUND(SUM(valor), 2) as total_faturado, COUNT(*) as qtd
FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.lancamentos`
WHERE tipo='entrada' AND status='RECEBIDO'
AND data_pagamento >= '{mes_inicio}' AND data_pagamento < '{prox_mes}'

"recebimentos previstos de março" ou "contas a receber" →
SELECT cliente_nome, ROUND(SUM(valor),2) as total, COUNT(*) as qtd
FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.lancamentos`
WHERE tipo='entrada' AND status IN ('A VENCER','VENCE HOJE','ATRASADO')
AND data_vencimento >= '{mes_inicio}' AND data_vencimento < '{prox_mes}'
GROUP BY cliente_nome ORDER BY total DESC LIMIT 20

"relação dos projetos de março" ou "projetos com emissão em março" →
SELECT projeto_nome, COUNT(*) as qtd, ROUND(SUM(valor),2) as total
FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.lancamentos`
WHERE data_emissao >= '{mes_inicio}' AND data_emissao < '{prox_mes}'
AND projeto_nome IS NOT NULL AND projeto_nome != 'Sem projeto'
GROUP BY projeto_nome ORDER BY total DESC

"contas a pagar vencidas" →
SELECT cliente_nome, ROUND(SUM(valor),2) as total, COUNT(*) as qtd
FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.lancamentos`
WHERE tipo='saida' AND status='ATRASADO'
GROUP BY cliente_nome ORDER BY total DESC LIMIT 20

"quanto devo pro fornecedor X" →
SELECT ROUND(valor,2) as valor, status, FORMAT_DATE('%d/%m/%Y', data_vencimento) as vencimento, categoria_nome
FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.lancamentos`
WHERE tipo='saida' AND LOWER(cliente_nome) LIKE LOWER('%X%')
AND status IN ('A VENCER','ATRASADO','VENCE HOJE')
ORDER BY data_vencimento

REGRAS OBRIGATÓRIAS:
1. Use SOMENTE SELECT
2. Tabelas: `{GCP_PROJECT_ID}.{BQ_DATASET}.<tabela>`
3. LIMIT 20 por padrão (a menos que peçam "todos")
4. Retorne APENAS o SQL puro, sem explicação, sem markdown, sem ```
5. Quando o usuário mencionar um mês sem ano, SEMPRE assuma {hoje.year}
6. "faturei", "faturamento", "NF" = entradas RECEBIDO, filtrar por data_pagamento
7. "paguei", "pagamentos" = saídas PAGO, filtrar por data_pagamento
8. "previstos", "a receber", "a pagar" = status IN ('A VENCER','ATRASADO','VENCE HOJE'), filtrar por data_vencimento
9. "recebimentos" sem qualificador = entradas com status RECEBIDO
10. Para nomes: LOWER(cliente_nome) LIKE LOWER('%termo%')
11. Se a pergunta é uma CONTINUAÇÃO da conversa anterior, use o mesmo contexto (datas, filtros)"""

        response = self.llm.generate("Você é um gerador de SQL BigQuery expert. Retorne SOMENTE o SQL puro, sem markdown.", prompt)
        sql = response.replace("```sql", "").replace("```", "").strip()
        return sql

    def is_safe_sql(self, sql: str) -> bool:
        """Valida que o SQL é somente leitura."""
        sql_upper = sql.upper().strip()
        if not sql_upper.startswith("SELECT"):
            return False
        dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "MERGE"]
        words = sql_upper.split()
        for word in dangerous:
            if word in words:
                return False
        if BQ_DATASET not in sql:
            return False
        return True

    def execute_query(self, sql: str) -> list[dict]:
        """Executa query no BigQuery com timeout."""
        try:
            job = self.bq.query(sql)
            rows = list(job.result(timeout=15))
            results = []
            for row in rows:
                d = dict(row)
                for k, v in d.items():
                    if hasattr(v, "isoformat"):
                        d[k] = v.isoformat()
                results.append(d)
            return results
        except Exception as e:
            return [{"erro": str(e)}]

    def financial_snapshot(self) -> dict:
        """Puxa snapshot financeiro completo para análise."""
        ds = f"`{GCP_PROJECT_ID}.{BQ_DATASET}"
        queries = {
            "resumo_mensal": f"""
                SELECT FORMAT_DATE('%Y-%m', data_pagamento) as mes,
                    SUM(CASE WHEN tipo='entrada' THEN valor ELSE 0 END) as receita,
                    SUM(CASE WHEN tipo='saida' THEN valor ELSE 0 END) as despesa,
                    SUM(CASE WHEN tipo='entrada' THEN valor ELSE -valor END) as resultado
                FROM {ds}.lancamentos`
                WHERE status IN ('PAGO','RECEBIDO') AND data_pagamento >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 MONTH)
                GROUP BY mes ORDER BY mes
            """,
            "saldos": f"""
                SELECT conta_nome, ROUND(saldo,2) as saldo, ROUND(diferenca,2) as dif_conciliacao
                FROM {ds}.saldos_bancarios` WHERE saldo != 0 ORDER BY ABS(saldo) DESC
            """,
            "a_receber": f"""
                SELECT ROUND(SUM(valor),2) as total, COUNT(*) as qtd,
                    ROUND(SUM(CASE WHEN status='ATRASADO' THEN valor ELSE 0 END),2) as atrasado,
                    COUNTIF(status='ATRASADO') as qtd_atrasado
                FROM {ds}.lancamentos` WHERE tipo='entrada' AND status IN ('A VENCER','ATRASADO','VENCE HOJE')
            """,
            "a_pagar": f"""
                SELECT ROUND(SUM(valor),2) as total, COUNT(*) as qtd,
                    ROUND(SUM(CASE WHEN status='ATRASADO' THEN valor ELSE 0 END),2) as atrasado,
                    COUNTIF(status='ATRASADO') as qtd_atrasado
                FROM {ds}.lancamentos` WHERE tipo='saida' AND status IN ('A VENCER','ATRASADO','VENCE HOJE')
            """,
            "top_despesas": f"""
                SELECT categoria_nome, ROUND(SUM(valor),2) as total
                FROM {ds}.lancamentos`
                WHERE tipo='saida' AND status='PAGO' AND data_pagamento >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 MONTH)
                GROUP BY categoria_nome ORDER BY total DESC LIMIT 10
            """,
            "top_clientes_receita": f"""
                SELECT cliente_nome, ROUND(SUM(valor),2) as total
                FROM {ds}.lancamentos`
                WHERE tipo='entrada' AND status='RECEBIDO' AND data_pagamento >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 MONTH)
                GROUP BY cliente_nome ORDER BY total DESC LIMIT 5
            """,
            "margem_projetos": f"""
                SELECT projeto_nome,
                    ROUND(SUM(CASE WHEN tipo='entrada' THEN valor ELSE 0 END),2) as receita,
                    ROUND(SUM(CASE WHEN tipo='saida' THEN valor ELSE 0 END),2) as custo
                FROM {ds}.lancamentos`
                WHERE projeto_nome != 'Sem projeto' AND status IN ('PAGO','RECEBIDO')
                    AND data_pagamento >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 MONTH)
                GROUP BY projeto_nome HAVING receita > 0 OR custo > 0
                ORDER BY receita DESC LIMIT 10
            """,
            "orcamento": f"""
                SELECT label, ROUND(SUM(valor_real),2) as real, ROUND(SUM(valor_bp),2) as bp
                FROM {ds}.orcamento_dre`
                WHERE mes_com_real = TRUE AND level = 0
                GROUP BY label ORDER BY label
            """,
        }

        snapshot = {}
        for name, sql in queries.items():
            try:
                rows = list(self.bq.query(sql).result(timeout=15))
                snapshot[name] = [dict(r) for r in rows]
            except Exception as e:
                snapshot[name] = [{"erro": str(e)}]
                log.warning(f"Snapshot query {name} falhou: {e}")

        return snapshot

    async def analyze_finances(self, question: str = "") -> str:
        """Análise financeira completa usando snapshot + LLM."""
        snapshot = self.financial_snapshot()

        import json
        snapshot_str = json.dumps(snapshot, ensure_ascii=False, default=str, indent=2)

        prompt = f"""Você é um consultor financeiro analisando os dados da empresa Studio Koti (marcenaria de alto padrão).

Dados financeiros atuais:
{snapshot_str}

{"Pergunta específica do dono: " + question if question else "Faça uma análise completa da saúde financeira."}

Sua análise deve incluir:

1. SAÚDE FINANCEIRA GERAL
   - Saldo de caixa atual e liquidez
   - Resultado operacional (receita vs despesa) dos últimos meses — tendência
   - Posição de contas a receber vs a pagar

2. PONTOS DE ATENÇÃO
   - Contas atrasadas (receber e pagar)
   - Diferenças de conciliação bancária
   - Concentração de receita em poucos clientes
   - Categorias de despesa crescendo

3. OPORTUNIDADES
   - Projetos com melhor/pior margem
   - Sugestões para melhorar fluxo de caixa
   - Real vs orçado — onde está acima/abaixo do esperado

Regras:
- Responda em português brasileiro, tom profissional mas acessível
- Use emojis para organizar seções
- Valores em R$ com formato brasileiro (ponto milhar, vírgula decimal)
- Seja específico com números, não genérico
- Se não tiver dados suficientes para algum ponto, pule
- Máximo 4000 caracteres (limite Telegram)
- NÃO use markdown com asteriscos"""

        return self.llm.generate(
            "Você é um consultor financeiro sênior. Analise dados reais e dê recomendações específicas.",
            prompt,
        )

    def format_response(self, question: str, sql: str, results: list[dict]) -> str:
        """Formata resultados em resposta amigável via LLM."""
        if not results:
            return "🔍 Não encontrei dados para essa consulta."
        if "erro" in results[0]:
            return f"❌ Erro na consulta: {results[0]['erro']}"

        prompt = f"""Pergunta do usuário: "{question}"

Resultado da query ({len(results)} linhas):
{results[:20]}

Formate uma resposta concisa em português brasileiro.
Use emojis para legibilidade.
Valores monetários em R$ com separador de milhar (ponto) e decimal (vírgula).
Se houver tabela, use formato limpo e alinhado.
Máximo 4000 caracteres (limite Telegram).
NÃO inclua o SQL na resposta.
NÃO use markdown com asteriscos."""

        return self.llm.generate(
            "Você é o assistente financeiro do Studio Koti. Responda de forma concisa e amigável.",
            prompt,
        )


# ============================================================
# TELEGRAM HANDLERS
# ============================================================

assistant: FinancialAssistant = None  # type: ignore


async def cmd_start(update, context):
    """Comando /start e /ajuda."""
    msg = """🏠 Koti Finance Bot

Sou o assistente financeiro do Studio Koti.
Pergunte qualquer coisa sobre as finanças!

📝 Exemplos:
• Quanto faturamos em fevereiro?
• Qual o saldo do BTG?
• Contas a pagar vencidas
• Top 5 clientes por receita
• Quanto devemos pro fornecedor X?
• Como está a saúde financeira da empresa?

⚡ Comandos:
/saldo — Saldos bancários
/analise — Análise financeira completa
/status — Último sync
/ajuda — Mais exemplos"""
    await update.message.reply_text(msg)


async def cmd_saldo(update, context):
    """Comando /saldo — saldos bancários direto."""
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    sql = f"""
        SELECT conta_nome, ROUND(saldo, 2) as saldo, ROUND(saldo_conciliado, 2) as conciliado,
               ROUND(diferenca, 2) as diferenca
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.saldos_bancarios`
        WHERE saldo != 0
        ORDER BY ABS(saldo) DESC
    """
    results = assistant.execute_query(sql)
    if not results or "erro" in results[0]:
        await update.message.reply_text("❌ Erro ao buscar saldos.")
        return

    total = sum(r.get("saldo", 0) for r in results)
    lines = ["🏦 Saldos Bancários (D-1)\n"]
    for r in results:
        saldo = r["saldo"]
        emoji = "🟢" if saldo > 0 else "🔴"
        lines.append(f"{emoji} {r['conta_nome']}: R$ {saldo:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    lines.append(f"\n💰 Total: R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    await update.message.reply_text("\n".join(lines))


async def cmd_status(update, context):
    """Comando /status — último sync."""
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    sql = f"""
        SELECT status, started_at, finished_at, duration_seconds,
               lancamentos_count, clientes_count
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.sync_log`
        ORDER BY started_at DESC LIMIT 1
    """
    results = assistant.execute_query(sql)
    if not results or "erro" in results[0]:
        await update.message.reply_text("❌ Erro ao buscar status.")
        return

    r = results[0]
    status = r.get("status", "?")
    icon = "✅" if status == "success" else "❌" if status == "failed" else "🔄"
    duration = r.get("duration_seconds", 0)
    lancamentos = r.get("lancamentos_count", 0)

    msg = f"""{icon} Último Sync: {status.upper()}
⏱ Duração: {duration}s
📊 Lançamentos: {lancamentos}
🕐 Início: {r.get('started_at', '?')}
🏁 Fim: {r.get('finished_at', '?')}"""
    await update.message.reply_text(msg)


async def cmd_analise(update, context):
    """Comando /analise — análise financeira completa."""
    chat_id = update.effective_chat.id
    if AUTHORIZED_CHAT_IDS and chat_id not in AUTHORIZED_CHAT_IDS:
        await update.message.reply_text("⛔ Acesso não autorizado.")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    await update.message.reply_text("🔄 Analisando dados financeiros... (pode levar ~15s)")
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    extra = " ".join(context.args) if context.args else ""
    response = await assistant.analyze_finances(extra)

    if len(response) > 4000:
        parts = [response[i:i+4000] for i in range(0, len(response), 4000)]
        for part in parts:
            await update.message.reply_text(part)
    else:
        await update.message.reply_text(response)

    log.info(f"[chat={chat_id}] Análise financeira enviada ({len(response)} chars)")


async def handle_message(update, context):
    """Handler para mensagens de texto (linguagem natural)."""
    chat_id = update.effective_chat.id

    # Verificar autorização
    if AUTHORIZED_CHAT_IDS and chat_id not in AUTHORIZED_CHAT_IDS:
        await update.message.reply_text("⛔ Acesso não autorizado.")
        log.warning(f"Acesso negado para chat_id={chat_id}")
        return

    # Indicar que está "digitando"
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    text = update.message.text
    log.info(f"[chat={chat_id}] Pergunta: {text}")

    # Buscar histórico de conversa
    history = chat_history.get(chat_id, [])

    # Detectar perguntas analíticas que precisam de snapshot completo
    analytical_keywords = ["saúde financeira", "saude financeira", "análise", "analise",
                          "oportunidade", "melhorar performance", "como está a empresa",
                          "como esta a empresa", "diagnóstico", "diagnostico",
                          "recomendação", "recomendacao", "ponto de atenção",
                          "ponto de atencao", "visão geral", "visao geral"]
    text_lower = text.lower()
    is_analytical = any(kw in text_lower for kw in analytical_keywords)

    if is_analytical:
        await update.message.reply_text("🔄 Analisando dados financeiros... (pode levar ~15s)")
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await assistant.analyze_finances(text)
    else:
        response = await assistant.process_message(text, history)

    # Salvar no histórico
    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": response[:200]})
    chat_history[chat_id] = history[-MAX_HISTORY * 2:]

    # Telegram max 4096 chars
    if len(response) > 4000:
        response = response[:4000] + "\n\n⚠️ Resposta truncada."

    await update.message.reply_text(response)
    log.info(f"[chat={chat_id}] Resposta enviada ({len(response)} chars)")


# ============================================================
# MAIN
# ============================================================

def init_assistant() -> FinancialAssistant:
    """Inicializa o assistente financeiro."""
    llm = GeminiProvider()
    bq_client = bigquery.Client(project=GCP_PROJECT_ID)
    return FinancialAssistant(llm, bq_client)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Bot Telegram Financeiro — Studio Koti")
    parser.add_argument("--local", action="store_true", help="Polling mode (dev)")
    parser.add_argument("--cli", action="store_true", help="CLI mode (sem Telegram)")
    args = parser.parse_args()

    global assistant
    assistant = init_assistant()

    if args.cli:
        # Modo interativo no terminal (com memória de conversa)
        print("🏠 Koti Finance Bot — Modo CLI")
        print("Digite 'quit' para sair.\n")
        cli_history: list[dict] = []
        while True:
            try:
                q = input("Pergunta> ").strip()
                if q.lower() in ("quit", "exit", "q"):
                    break
                if not q:
                    continue
                response = asyncio.run(assistant.process_message(q, cli_history))
                cli_history.append({"role": "user", "content": q})
                cli_history.append({"role": "assistant", "content": response[:200]})
                cli_history = cli_history[-MAX_HISTORY * 2:]
                print(f"\n{response}\n")
            except (KeyboardInterrupt, EOFError):
                break

    elif args.local:
        # Polling mode (dev)
        from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

        if not TELEGRAM_TOKEN:
            print("❌ Configure TELEGRAM_BOT_TOKEN!")
            sys.exit(1)

        print("🏠 Koti Finance Bot — Polling mode")
        print(f"   Projeto: {GCP_PROJECT_ID}")
        print(f"   Dataset: {BQ_DATASET}")
        if AUTHORIZED_CHAT_IDS:
            print(f"   Chat IDs autorizados: {AUTHORIZED_CHAT_IDS}")
        else:
            print("   Chat IDs autorizados: todos")
        print("   Aguardando mensagens...\n")

        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(CommandHandler("saldo", cmd_saldo))
        app.add_handler(CommandHandler("analise", cmd_analise))
        app.add_handler(CommandHandler("status", cmd_status))
        app.add_handler(CommandHandler("ajuda", cmd_start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.run_polling()

    else:
        print("Uso: python bot_telegram.py --local (Telegram) ou --cli (terminal)")
        print("  --local  Conecta ao Telegram via polling")
        print("  --cli    Modo interativo no terminal")


if __name__ == "__main__":
    main()
