# Dashboard Koti — Claude Code Instructions

Instructions for agents working on the Studio Koti dashboard. Bias toward caution, small diffs and explicit financial semantics.

## Behavioral Rules

### 1. Think Before Coding

- State relevant assumptions before implementing.
- If multiple interpretations exist, surface them instead of choosing silently.
- If something is unclear, stop and ask.
- Prefer the simpler and safer approach when it satisfies the task.

### 2. Simplicity First

- No features beyond what was asked.
- No abstractions for single-use code.
- No speculative configurability.
- No broad refactors for narrow tasks.
- If a change can be small, keep it small.

### 3. Surgical Changes

- Touch only files required by the task.
- Match existing style.
- Do not reformat adjacent code.
- Do not delete unrelated dead code; mention it instead.
- Remove only code made unused by your own change.
- Every changed line must trace to the task or spec.

### 4. Verify The Goal

For multi-step work, use a short plan:

```text
1. [Step] -> verify: [check]
2. [Step] -> verify: [check]
3. [Step] -> verify: [check]
```

If success criteria are vague, clarify before implementing.

## First Action

Before non-trivial work, read:

- Obsidian specs: `Projetos/Studio Koti/Dashboard/Execucao Opus/specs/`
- Execution docs: `Projetos/Studio Koti/Dashboard/Execucao Opus/`
- Repo docs when relevant: `ARCHITECTURE.md`, `IMPLEMENTACAO_BQ.md`, `RUNBOOK.md`
- Skills when relevant: `.claude/skills/`

Follow the spec over conversation memory. If spec and request conflict, ask.

## Hard Rules

- Do not push without explicit approval.
- Do not merge into `main` without explicit approval.
- Do not deploy Cloud Run, Cloud Functions or BigQuery uploads without explicit approval.
- Do not commit `.env`, secrets, credentials, `.backup*`, `rh_data.json`, raw private data or generated local files.
- Do not invent data.
- Do not create Monday/commercial pipeline features in this phase.
- Do not delete old dashboard tabs in the executive-layer phase.

Local commits are allowed only when the task/spec asks for them. Push is not.

## Data Semantics

Always distinguish:

- caixa realizado;
- caixa previsto;
- competencia;
- orcamento/BP;
- faturamento direto;
- RH/folha.

Do not label a metric as `Receita`, `EBITDA`, `Lucro Liquido`, `Margem` or `Caixa Liquido` unless the calculation supports that exact meaning.

If a metric mixes regimes, rename it, split it, or stop and ask.

If future months appear, clarify whether they are forecast, competence, budget/BP or pending Omie entries.

Prefer explicit labels when accurate:

- `Receita Operacional`
- `Resultado Operacional`
- `Caixa Total`
- `Caixa Liquido`
- `EBITDA`
- `Lucro Liquido`

## Dashboard Phase

Current strategy: add executive tabs at the beginning while preserving old tabs as detail.

Planned executive tabs:

| Tab | Regime | Status |
|---|---|---|
| Executivo | Misto, with explicit caixa vs competencia labels | In progress |
| Caixa | Caixa | Planned |
| Resultado | Competencia | Planned |
| Projetos & Fechamentos | Competencia | Planned |
| FD / Conciliacao | Competencia | Planned |
| Pessoas & Folha | RH / folha | Planned |

Preserve:

- old `data-tab` values;
- Visao Geral antiga;
- Fluxo de Caixa mes a mes;
- FD / Conciliacao;
- RH current flow.

For dashboard code:

- Do not duplicate HTML ids or canvas ids.
- Store new charts in `charts` so `destroyCharts()` cleans them.
- Do not change `compute()` or `computeCompetencia()` unless the spec explicitly requires it.

## Specs Workflow

Before implementing:

- Read the relevant spec in `Projetos/Studio Koti/Dashboard/Execucao Opus/specs/`.
- If there is no spec for a non-trivial task, stop and ask for one.
- Confirm assumptions when data, credentials or access are unclear.

After implementing:

- Fill `Resultado da execucao` in the spec.
- Update `Execucao Opus/05-log-execucao.md`.
- Update `Execucao Opus/specs/INDEX.md`.
- Update `Execucao Opus/06-decisoes-abertas.md` if there is a blocker, decision, risk or scope suggestion.
- Stop and report. Do not push.

## Validation

Prefer validation without installing new dependencies. Ask before installing.

For dashboard UI changes, validate:

- duplicate ids;
- old tabs still open;
- new charts are registered in `charts`;
- `compute()` and `computeCompetencia()` are unchanged unless required;
- local visual check before any push.

## Project References

- Dashboard: `dashboard_bq.html`
- RH dashboard: `dashboard_rh.html`
- BigQuery API: `api_bq.py`
- Omie sync: `omie_sync_bq.py`
- RH extract: `extract_rh.py`
- BP extract: `extract_bp_bq.py`
- Architecture: `ARCHITECTURE.md`
- Implementation: `IMPLEMENTACAO_BQ.md`
- Operations: `RUNBOOK.md`
