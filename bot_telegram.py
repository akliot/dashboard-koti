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
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "dashboard-koti-omie")
BQ_DATASET = os.environ.get("BQ_DATASET", "studio_koti")
BRT = timezone(timedelta(hours=-3))

# IDs autorizados (vazio = todos podem usar)
_auth_ids = os.environ.get("AUTHORIZED_CHAT_IDS", "")
AUTHORIZED_CHAT_IDS = set(int(x.strip()) for x in _auth_ids.split(",") if x.strip()) if _auth_ids else set()

# Diretoria — acesso a folha_funcionarios e saldos_bancarios
_admin_id = os.environ.get("ADMIN_CHAT_ID", "")
EXEC_CHAT_IDS = {int(_admin_id)} if _admin_id else set()
_exec_ids = os.environ.get("EXEC_CHAT_IDS", "")
if _exec_ids:
    EXEC_CHAT_IDS |= set(int(x.strip()) for x in _exec_ids.split(",") if x.strip())

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

def get_schema_context(is_exec: bool = False) -> str:
    base = f"""Tabelas no dataset `{GCP_PROJECT_ID}.{BQ_DATASET}`:

1. lancamentos: id INT64, tipo STRING (entrada/saida), valor FLOAT64, status STRING (PAGO/RECEBIDO/A VENCER/ATRASADO/VENCE HOJE/CANCELADO), data_vencimento DATE, data_emissao DATE, data_pagamento DATE (data real do pagamento/recebimento, NULL se pendente), data_previsao DATE (data prevista de pagamento — dia útil real, para pendentes), numero_documento STRING, categoria_codigo STRING, categoria_nome STRING, categoria_grupo STRING, projeto_id INT64, projeto_nome STRING, cliente_id INT64, cliente_nome STRING, is_faturamento_direto BOOL

2. saldos_bancarios: conta_id INT64, conta_nome STRING, conta_tipo STRING, saldo FLOAT64, saldo_conciliado FLOAT64, diferenca FLOAT64, data_referencia DATE

3. categorias: codigo STRING, nome STRING, grupo STRING

4. projetos: id INT64, nome STRING

5. clientes: id INT64, nome_fantasia STRING, razao_social STRING, estado STRING, ativo BOOL, pessoa_fisica BOOL

6. vendas_pedidos: pedido_id INT64, valor_mercadorias FLOAT64, etapa STRING, produto_descricao STRING, produto_quantidade FLOAT64

7. orcamento_dre: label STRING, section STRING, level INT64, mes STRING, valor_real FLOAT64, valor_bp FLOAT64, variacao_pct FLOAT64, mes_com_real BOOL

8. sync_log: sync_id STRING, status STRING, started_at TIMESTAMP, finished_at TIMESTAMP, duration_seconds INT64"""

    if is_exec:
        base += f"""

9. folha_funcionarios: nome STRING, departamento STRING, cargo STRING, data_admissao DATE, idade INT64, tempo_casa_meses FLOAT64, salario FLOAT64, comissao FLOAT64, bonus FLOAT64, rescisao FLOAT64, beneficios FLOAT64 (Caju+VT+Estac+Clínica+Gympass), custo_total FLOAT64, mes_referencia STRING (2026-01 etc), status STRING (realizado/andamento/projecao)

Exemplos de queries para folha_funcionarios:
- "quanto ganha X" → SELECT nome, cargo, departamento, salario, beneficios, custo_total FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.folha_funcionarios` WHERE LOWER(nome) LIKE '%x%' AND mes_referencia = FORMAT_DATE('%Y-%m', CURRENT_DATE())
- "custo do departamento X" → SELECT departamento, COUNT(*) as qtd, ROUND(SUM(custo_total),2) as custo, ROUND(AVG(salario),2) as sal_medio FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.folha_funcionarios` WHERE LOWER(departamento) LIKE '%x%' AND mes_referencia = FORMAT_DATE('%Y-%m', CURRENT_DATE()) GROUP BY departamento
- "maiores salários" → SELECT nome, cargo, departamento, salario FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.folha_funcionarios` WHERE mes_referencia = FORMAT_DATE('%Y-%m', CURRENT_DATE()) ORDER BY salario DESC LIMIT 10
- Se o usuário pedir mês específico (ex: "folha de janeiro", "salários em fevereiro"), usar WHERE mes_referencia = 'YYYY-MM' com o mês solicitado (ex: '2026-01', '2026-02')"""
    else:
        base += """

RESTRIÇÕES DE ACESSO:
- NUNCA consultar a tabela folha_funcionarios — dados de folha de pagamento são restritos à diretoria
- NUNCA consultar a tabela saldos_bancarios — saldos bancários são restritos à diretoria
- Se o usuário pedir informações de folha, salários, funcionários ou saldos bancários, responda: "⛔ Acesso restrito à diretoria."
- Foque apenas em: lancamentos, categorias, projetos, clientes, vendas_pedidos, orcamento_dre"""

    base += f"""

Notas:
- tipo 'entrada' = receita, 'saida' = despesa
- Status PAGO = saída liquidada, RECEBIDO = entrada liquidada
- A data de hoje é {date.today().isoformat()}
- Sempre usar backticks para referências de tabela: `{GCP_PROJECT_ID}.{BQ_DATASET}.nome_tabela`
"""
    return base


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
        import time
        for attempt in range(3):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=f"{system_prompt}\n\n{user_prompt}",
                )
                return response.text
            except Exception as e:
                if "429" in str(e) and attempt < 2:
                    wait = 25 * (attempt + 1)
                    log.warning(f"Rate limit, aguardando {wait}s... (tentativa {attempt+1}/3)")
                    time.sleep(wait)
                else:
                    raise


class ClaudeProvider(LLMProvider):
    """Provedor Anthropic Claude via SDK oficial."""

    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model_name = "claude-haiku-4-5-20251001"
        log.info(f"Claude provider inicializado ({self.model_name})")

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        msg = self.client.messages.create(
            model=self.model_name,
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return msg.content[0].text


# ============================================================
# FINANCIAL ASSISTANT
# ============================================================

class FinancialAssistant:
    """Assistente financeiro que converte linguagem natural → SQL → resposta."""

    def __init__(self, llm: LLMProvider, bq_client: bigquery.Client):
        self.llm = llm
        self.bq = bq_client
        self._is_exec = False  # set per-request by handle_message
        self.schema_context = get_schema_context(is_exec=False)

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
            # Montar contexto de conversa (inclui SQL anterior para continuidade)
            history_context = ""
            if history:
                lines = []
                for m in history[-8:]:
                    prefix = 'Usuário' if m['role']=='user' else 'Bot'
                    lines.append(f"{prefix}: {m['content']}")
                    if m.get('sql'):
                        lines.append(f"  [SQL usado: {m['sql']}]")
                history_context = "Conversa anterior (MANTENHA os mesmos filtros de data/período se a pergunta for continuação):\n" + "\n".join(lines) + "\n\n"

            # 1. Gerar SQL via LLM
            sql = self.generate_sql(text, history_context)
            log.info(f"SQL gerado: {sql[:200]}")
            self._last_sql = sql  # Salvar para o histórico

            # 2. Validar SQL (safety)
            if not self.is_safe_sql(sql):
                # Checar se é restrição de acesso (não-exec tentando folha/saldos)
                if not self._is_exec:
                    sql_lower = sql.lower()
                    if "folha_funcionarios" in sql_lower or "saldos_bancarios" in sql_lower:
                        return "⛔ Acesso restrito à diretoria. Dados de folha e saldos bancários não estão disponíveis para este chat."
                return "⚠️ Só posso executar consultas de leitura (SELECT)."

            # 3. Executar no BQ
            results = self.execute_query(sql)

            # 4. Se 0 resultados, tentar desambiguar ou oferecer alternativas
            if not results or len(results) == 0:
                # Buscar projetos/clientes similares para desambiguação
                disambig = self.disambiguate(text)
                if disambig:
                    return disambig
                return "🔍 Não encontrei dados para essa consulta. Tente reformular a pergunta com mais detalhes."

            # 5. Formatar resposta via LLM
            return self.format_response(text, sql, results)

        except Exception as e:
            log.error(f"Erro ao processar: {e}")
            return f"❌ Erro ao processar sua pergunta: {e}"

    def disambiguate(self, question: str) -> str | None:
        """Busca projetos/clientes similares para desambiguação.
        Retorna mensagem com opções, ou None se não encontrou nada."""
        words = [w for w in question.lower().split()
                 if len(w) > 2 and w not in self._STOPWORDS]
        if not words:
            return None

        # Montar filtro: cada palavra deve estar no nome
        # "nex one 1513" → LOWER(nome) LIKE '%nex%' AND LIKE '%one%' AND LIKE '%1513%'
        like_clauses_proj = " AND ".join(f"LOWER(projeto_nome) LIKE '%{w}%'" for w in words[:4])
        like_clauses_cli = " AND ".join(f"LOWER(cliente_nome) LIKE '%{w}%'" for w in words[:4])

        results = []
        try:
            # Buscar projetos
            q = f"""SELECT DISTINCT projeto_nome as nome, 'projeto' as tipo
                    FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.lancamentos`
                    WHERE {like_clauses_proj} AND projeto_nome != 'Sem projeto'
                    LIMIT 10"""
            for row in self.bq.query(q).result(timeout=5):
                results.append((row.nome, row.tipo))
        except Exception:
            pass

        try:
            # Buscar clientes
            q = f"""SELECT DISTINCT cliente_nome as nome, 'cliente' as tipo
                    FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.lancamentos`
                    WHERE {like_clauses_cli} AND cliente_nome != ''
                    LIMIT 10"""
            for row in self.bq.query(q).result(timeout=5):
                results.append((row.nome, row.tipo))
        except Exception:
            pass

        if not results:
            # Tentar com menos palavras (só as mais longas)
            long_words = [w for w in words if len(w) > 3][:2]
            if long_words and long_words != words:
                for w in long_words:
                    try:
                        q = f"""SELECT DISTINCT projeto_nome as nome FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.lancamentos`
                                WHERE LOWER(projeto_nome) LIKE '%{w}%' AND projeto_nome != 'Sem projeto' LIMIT 5"""
                        for row in self.bq.query(q).result(timeout=5):
                            results.append((row.nome, 'projeto'))
                        q = f"""SELECT DISTINCT cliente_nome as nome FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.lancamentos`
                                WHERE LOWER(cliente_nome) LIKE '%{w}%' AND cliente_nome != '' LIMIT 5"""
                        for row in self.bq.query(q).result(timeout=5):
                            results.append((row.nome, 'cliente'))
                    except Exception:
                        pass

        if not results:
            return None

        # Deduplicate
        seen = set()
        unique = []
        for nome, tipo in results:
            if nome not in seen:
                seen.add(nome)
                unique.append((nome, tipo))

        if len(unique) == 1:
            # Só 1 match — provavelmente é o que o usuário quer, mas a SQL não encontrou
            nome, tipo = unique[0]
            return f"🔍 Encontrei '{nome}' mas a consulta não retornou dados. Tente perguntar de outra forma, por exemplo:\n• Quanto gastei no projeto {nome}?"

        # Múltiplos matches — pedir desambiguação
        lines = ["🔍 Encontrei vários resultados similares. Qual deles você quer?\n"]
        for i, (nome, tipo) in enumerate(unique[:10], 1):
            emoji = "📁" if tipo == 'projeto' else "👤"
            lines.append(f"{emoji} {i}. {nome}")
        lines.append("\nResponda com o nome completo ou o número.")
        return "\n".join(lines)

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
8. "previstos", "a receber", "a pagar", "pagar hoje" = status IN ('A VENCER','ATRASADO','VENCE HOJE'), filtrar por data_previsao (dia útil real, não data_vencimento)
9. "recebimentos" sem qualificador = entradas com status RECEBIDO
10. Para buscar nomes de clientes/fornecedores: OBRIGATÓRIO usar LOWER(cliente_nome) LIKE LOWER('%termo%') — BigQuery LIKE é case-sensitive!
11. REGRA CRÍTICA DE CONTEXTO: Se a pergunta é curta ou usa pronomes como "desse", "disso", "já", "esse valor", "deles", ela é CONTINUAÇÃO da conversa anterior. Nesse caso:
    - COPIE os filtros de data do SQL anterior (ex: data_vencimento >= '2026-03-01' AND data_vencimento < '2026-04-01')
    - MANTENHA o mesmo período temporal
    - Apenas mude o que a nova pergunta pede (ex: de A VENCER para RECEBIDO)
    - Exemplo: se perguntou "recebimentos previstos de março" (status IN 'A VENCER') e depois "quanto já recebi?", o SQL deve ser:
      SELECT ROUND(SUM(valor),2) FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.lancamentos` WHERE tipo='entrada' AND status='RECEBIDO' AND data_pagamento >= '{mes_inicio}' AND data_pagamento < '{prox_mes}'
    - NUNCA remova filtros de data em follow-ups
12. "gastei de X" ou "paguei pra X" = saídas PAGO com LOWER(cliente_nome) LIKE LOWER('%X%')
13. Para buscar nomes com MÚLTIPLAS PALAVRAS (ex: 'nex one 1513'), use AND entre cada palavra:
    LOWER(projeto_nome) LIKE '%nex%' AND LOWER(projeto_nome) LIKE '%one%' AND LOWER(projeto_nome) LIKE '%1513%'
    NUNCA junte tudo em um único LIKE '%nex one 1513%' — os nomes no banco têm separadores como | e espaços extras

REGRAS DE SINTAXE BIGQUERY (NÃO use sintaxe MySQL):
- NÃO use GROUP_CONCAT → use STRING_AGG(campo, ', ')
- NÃO use YEAR(data) → use EXTRACT(YEAR FROM data)
- NÃO use MONTH(data) → use EXTRACT(MONTH FROM data)
- NÃO use DATE_FORMAT → use FORMAT_DATE
- NÃO use LIMIT com offset → BigQuery não suporta LIMIT x,y
- NÃO use NOW() → use CURRENT_TIMESTAMP() ou CURRENT_DATE()

REGRAS DE BUSCA DE NOMES (IMPORTANTE):
- "kairos", "castini", "norte sul", "alpha1" e qualquer nome de empresa/pessoa = SEMPRE buscar em cliente_nome
- Nomes de empresas/pessoas SEMPRE vão em cliente_nome, NUNCA em categoria_nome
- Categorias são termos genéricos como "Marcenaria", "Mão de Obra", "Civil", "Aluguel", "SG&A"
- Se a pergunta diz "gastei de X" ou "paguei pra X", X é um cliente/fornecedor → buscar em cliente_nome
- "mão de obra" é uma CATEGORIA, não um fornecedor → buscar em LOWER(categoria_nome) LIKE LOWER('%m_o de obra%') OR LOWER(categoria_nome) LIKE LOWER('%mao de obra%')"""

        response = self.llm.generate("Você é um gerador de SQL BigQuery expert. Retorne SOMENTE o SQL puro, sem markdown.", prompt)
        sql = response.replace("```sql", "").replace("```", "").strip()
        return sql

    def is_safe_sql(self, sql: str) -> bool:
        """Valida que o SQL é somente leitura e respeita nível de acesso."""
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
        # Tabelas restritas — só exec pode consultar
        if not self._is_exec:
            restricted = ["folha_funcionarios", "saldos_bancarios"]
            sql_lower = sql.lower()
            for tbl in restricted:
                if tbl in sql_lower:
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
    is_exec = chat_id in EXEC_CHAT_IDS
    log.info(f"[chat={chat_id}] Pergunta: {text} (exec={is_exec})")

    # Atualizar contexto de schema conforme nível de acesso
    assistant._is_exec = is_exec
    assistant.schema_context = get_schema_context(is_exec=is_exec)

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

    # Salvar no histórico (incluindo SQL para contexto)
    last_sql = getattr(assistant, '_last_sql', '')
    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": response[:500], "sql": last_sql})
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
    """Inicializa o assistente financeiro. Claude se tiver key, senão Gemini."""
    if ANTHROPIC_API_KEY:
        llm = ClaudeProvider()
    elif GEMINI_API_KEY:
        llm = GeminiProvider()
    else:
        print("❌ Configure ANTHROPIC_API_KEY ou GEMINI_API_KEY!")
        sys.exit(1)
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
                cli_history.append({"role": "assistant", "content": response[:500], "sql": getattr(assistant, '_last_sql', '')})
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
