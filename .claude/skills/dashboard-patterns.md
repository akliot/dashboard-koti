# Padrões do Dashboard HTML

## Stack
- Single-page HTML (`dashboard_bq.html`, ~1500 linhas)
- Chart.js + chartjs-plugin-datalabels
- Vanilla JS (sem frameworks)
- GitHub Pages (deploy) → fetch da Cloud Function

## Regimes de dados

| Regime | Abas | Campo de filtro | Exclui ATRASADO? |
|--------|------|----------------|:----------------:|
| **Caixa** | 1 (Visão Geral), 2 (Fluxo), 4 (Conciliação) | `l.data` (pagamento real) | Não |
| **Competência** | 3 (Financeiro), 5 (Vendas), 7 (Projetos) | `l.data_vencimento` | Sim |
| **Pendentes** | — | `l.data_previsao` (dia útil real) | — |

`aplicarFiltros()` cria 2 arrays:
- `dadosFiltrados.lancamentos` → regime caixa
- `dadosFiltrados.lancamentos_competencia` → regime competência

## Como adicionar um KPI card

HTML dentro de `.kpi-row`:
```html
<div class="kpi-card">
  <div class="kpi-label">Nome</div>
  <div class="kpi-value green">R$ 1.234</div>
  <div class="kpi-sub">Detalhes</div>
</div>
```

JS dentro de `renderVisaoGeral(d)`:
```javascript
document.getElementById('kpi-geral').innerHTML += `
  <div class="kpi-card">...</div>`;
```

## Como adicionar um gráfico

HTML:
```html
<div class="chart-card">
  <h3>Título</h3>
  <div class="chart-container"><canvas id="chartNome"></canvas></div>
</div>
```

JS — sempre destruir antes de criar:
```javascript
if(charts.nome){charts.nome.destroy();delete charts.nome}
charts.nome = new Chart(document.getElementById('chartNome'), {
  type: 'bar', data: {...}, options: {...}
});
```

## Como adicionar um toggle

HTML:
```html
<div class="periodo-btns" id="nomeBtns">
  <button onclick="setNomeMode('a',this)" class="active">A</button>
  <button onclick="setNomeMode('b',this)">B</button>
</div>
```

JS:
```javascript
let _nomeData=null, _nomeMode='a';
function setNomeMode(mode,el){
  _nomeMode=mode;
  document.querySelectorAll('#nomeBtns button').forEach(b=>b.classList.remove('active'));
  if(el)el.classList.add('active');
  renderNome();
}
```

## Cores padrão

- Entrada/receita: `#22c55e` (verde)
- Saída/despesa: `#ef4444` (vermelho)
- Info/neutro: `#3b82f6` (azul)
- FD (claro): `#22c55e55` / `#ef444455`
- SK (forte): `#22c55ecc` / `#ef4444cc`

## Validação JS

Após editar, sempre validar com Node:
```bash
node -e "const fs=require('fs');const h=fs.readFileSync('dashboard_bq.html','utf8');const m=h.match(/<script>([\s\S]*?)<\/script>/g);try{new Function(m[1].replace('<script>','').replace('</script>',''));console.log('OK')}catch(e){console.log(e.message)}"
```

## Cloud Function (api_bq.py)

- GET `/api/dashboard` → JSON completo
- CORS: `ALLOWED_ORIGINS` (só github.io + localhost)
- Deploy: `gcloud functions deploy api_dashboard --gen2 --memory 512MB`
- Após mudanças na API: **redeploy obrigatório**
