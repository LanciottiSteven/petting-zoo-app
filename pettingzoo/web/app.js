const $  = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const api = async (url, opts) => {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.json().catch(()=>({detail:r.statusText}))).detail || r.statusText);
  return r.json();
};
const post = (url, body) => api(url, {method:'POST', headers:{'Content-Type':'application/json'},
                                      body: JSON.stringify(body)});
const fmt = (v,d=1) => (v===null||v===undefined) ? '—' : Number(v).toFixed(d);
const posCls = p => p === 'D/ST' ? 'DST' : p;
const norm = s => (s||'').normalize('NFKD').replace(/[̀-ͯ]/g,'')
  .toLowerCase().replace(/\b(jr|sr|ii|iii|iv|v)\b\.?/g,'').replace(/[^a-z]/g,'');

let LEAGUE = {}, PLAYERS = [], STATE = {taken:[], my_roster:[], pick_no:1}, HISTORY = [];

/* ------------------------------------------------------------- tabs */
$$('nav button').forEach(b => b.onclick = () => {
  $$('nav button').forEach(x=>x.classList.toggle('on', x===b));
  $$('.panel').forEach(p=>p.classList.toggle('on', p.id === 'tab-'+b.dataset.tab));
  if (b.dataset.tab === 'alerts') loadFlags();
  if (b.dataset.tab === 'runs') loadRuns();
  if (b.dataset.tab === 'players') renderPlayers();
  if (b.dataset.tab === 'strategies') loadStrategies();
});

/* ------------------------------------------------------------- boot */
async function boot(){
  const sel = $('#slot');
  sel.innerHTML = '<option value="">— set —</option>' +
    Array.from({length:10},(_,i)=>`<option value="${i+1}">Pick ${i+1}</option>`).join('');
  try {
    STATE = await api('/api/state');
    HISTORY = [];
  } catch(e){}
  LEAGUE = await api('/api/league');
  $('#leagueSub').textContent =
    `${LEAGUE.n_teams} teams · full PPR · ${LEAGUE.roster_size}-man roster · draft ${new Date(LEAGUE.draft_date).toLocaleString()}`;
  const slot = STATE.my_slot || LEAGUE.my_slot;
  if (slot) sel.value = slot;
  stamp(LEAGUE.built_at);
  const d = await api('/api/players?limit=700');
  PLAYERS = d.players;
  renderAll();
}
function stamp(ts){
  if(!ts) return;
  const mins = Math.round((Date.now()/1000 - ts)/60);
  $('#freshness').textContent = mins < 1 ? 'data just refreshed' : `data ${mins}m old`;
}
$('#slot').onchange = () => { saveState(); renderBoard(); };
$('#refreshBtn').onclick = async e => {
  const b = e.target; b.disabled = true; const t = b.textContent;
  b.innerHTML = '<span class="spin"></span>Refreshing…';
  try {
    const r = await post('/api/refresh', {force:true});
    PLAYERS = (await api('/api/players?limit=700')).players;
    LEAGUE = await api('/api/league'); stamp(LEAGUE.built_at); renderAll();
    if (Object.keys(r.errors||{}).length) alert('Some sources failed:\n'+JSON.stringify(r.errors,null,1));
  } catch(err){ alert('Refresh failed: '+err.message); }
  b.disabled = false; b.textContent = t;
};

/* ------------------------------------------------------------- state */
const mySlot = () => Number($('#slot').value) || null;
const takenSet = () => new Set(STATE.taken.map(norm));
const available = () => PLAYERS.filter(p => !takenSet().has(norm(p.name)));
function saveState(){
  STATE.my_slot = mySlot();
  STATE.pick_no = STATE.taken.length + 1;
  post('/api/state', STATE).catch(()=>{});
}
function draft(name, mine){
  HISTORY.push(JSON.parse(JSON.stringify(STATE)));
  STATE.taken.push(name);
  if (mine) STATE.my_roster.push(name);
  saveState(); renderAll();
}
$('#undoBtn').onclick = () => {
  if (!HISTORY.length) return;
  STATE = HISTORY.pop(); saveState(); renderAll();
};

/* ------------------------------------------------------------- render */
function renderAll(){ renderBoard(); renderPlayers(); }

function renderBoard(){
  const slot = mySlot();
  const pick = STATE.taken.length + 1;
  $('#pickNo').textContent = pick;
  const round = Math.ceil(pick/10), inRound = ((pick-1)%10)+1;
  const onClock = round % 2 ? inRound : 11-inRound;
  $('#pickWho').textContent = `round ${round}, pick ${inRound}` +
    (slot ? (onClock === slot ? ' — YOU ARE UP' : ` — slot ${onClock}`) : '');
  $('#pickWho').className = (slot && onClock===slot) ? 'sub good' : 'sub';

  if (slot){
    const picks = []; for(let r=0;r<14;r++)
      picks.push(r%2===0 ? r*10+slot : r*10+(11-slot));
    const next = picks.filter(p => p >= pick);
    $('#nextPick').textContent = next.length ? next[0] : '—';
    $('#allPicks').textContent = 'then ' + (next.slice(1,5).join(', ') || '—');
  } else { $('#nextPick').textContent='—'; $('#allPicks').textContent='set your slot above'; }

  const mine = PLAYERS.filter(p => STATE.my_roster.map(norm).includes(norm(p.name)));
  $('#rosterCount').textContent = `${mine.length}/14`;
  const counts = {};
  mine.forEach(p => counts[p.pos] = (counts[p.pos]||0)+1);
  const need = [];
  Object.entries({QB:1,RB:2,WR:2,TE:1,K:1,'D/ST':1}).forEach(([k,v])=>{
    const short = v - (counts[k]||0); if (short>0) need.push(`${short} ${k}`);
  });
  $('#needs').textContent = need.length ? 'still need ' + need.join(', ') : 'starters complete';

  const rb = $('#boardBody'); const q = norm($('#search').value);
  const pf = $('#posFilter').value;
  const list = available().filter(p => (!pf || p.pos===pf) && (!q || norm(p.name).includes(q)))
                          .sort((a,b)=>b.vor-a.vor).slice(0,120);
  rb.innerHTML = list.map(p => `<tr>
    <td>${p.name}${flagHtml(p)}</td>
    <td><span class="pill ${posCls(p.pos)}">${p.pos}${p.pos_rank||''}</span></td>
    <td class="num">${fmt(p.proj)}</td><td class="num">${fmt(p.vor)}</td>
    <td class="num">${p.adp>900?'—':fmt(p.adp)}</td><td class="num">${p.bye||'—'}</td>
    <td style="white-space:nowrap">
      <button onclick="draft(${JSON.stringify(p.name).replace(/"/g,'&quot;')},false)">Taken</button>
      <button class="primary" onclick="draft(${JSON.stringify(p.name).replace(/"/g,'&quot;')},true)">Mine</button>
    </td></tr>`).join('') || '<tr><td colspan="7" class="empty">No players match.</td></tr>';

  $('#rosterEmpty').style.display = mine.length ? 'none':'block';
  $('#myRoster').innerHTML = mine.map(p=>`<tr>
    <td><span class="pill ${posCls(p.pos)}">${p.pos}</span></td>
    <td>${p.name}${flagHtml(p)}</td><td class="num">${fmt(p.proj)}</td>
    <td class="num dim">bye ${p.bye||'—'}</td></tr>`).join('');
  renderLineup(mine);
}
function flagHtml(p){
  if (p.games_missed) return ` <span class="flag">OUT ${p.games_missed}G</span>`;
  if (!p.flag) return '';
  const soft = /question|doubt/i.test(p.flag);
  return ` <span class="${soft?'flagq':'flag'}">${p.flag}</span>`;
}
function renderLineup(mine){
  const by = {}; mine.forEach(p => (by[p.pos] = by[p.pos]||[]).push(p));
  Object.values(by).forEach(l => l.sort((a,b)=>b.proj-a.proj));
  const used = new Set(); const rows = [];
  [['QB',1],['RB',2],['WR',2],['TE',1],['K',1],['D/ST',1]].forEach(([pos,n])=>{
    for(let i=0;i<n;i++){
      const p = (by[pos]||[])[i];
      if (p) used.add(p.name);
      rows.push([pos, p]);
    }
  });
  const flex = mine.filter(p=>['RB','WR','TE'].includes(p.pos) && !used.has(p.name))
                   .sort((a,b)=>b.proj-a.proj)[0];
  rows.push(['FLEX', flex]);
  let total = 0; rows.forEach(([,p]) => { if(p) total += p.proj; });
  $('#myLineup').innerHTML = rows.map(([slot,p])=>`<tr>
    <td class="tag">${slot}</td>
    <td>${p ? p.name : '<span class="dim">— empty —</span>'}</td>
    <td class="num">${p?fmt(p.proj):''}</td></tr>`).join('') +
    `<tr><td></td><td><b>Total</b></td><td class="num"><b>${fmt(total)}</b></td></tr>`;
}
$('#search').oninput = renderBoard; $('#posFilter').onchange = renderBoard;

/* ------------------------------------------------------------- recommend */
$('#recBtn').onclick = async e => {
  const slot = mySlot();
  if (!slot) return alert('Set your draft slot first.');
  const b = e.target; b.disabled = true;
  const dyn = $('#advMode').checked;
  $('#recStatus').innerHTML = '<span class="spin"></span>' +
    (dyn ? 'reading the board…' : 'simulating…');
  $('#adviceOut').innerHTML = ''; $('#posBoard').innerHTML = '';
  try {
    const body = {my_slot: slot, taken: STATE.taken, my_roster: STATE.my_roster,
                  pick_no: STATE.taken.length+1, n_sims: Number($('#recSims').value)};
    if (dyn){
      const a = await post('/api/advise', {...body, scan_sims:80, top_k:6});
      renderAdvice(a);
      $('#recStatus').textContent = `round ${a.round} · next pick ${a.next_pick||'—'}`;
    } else {
      const r = await post('/api/recommend', {...body, top_k:8, save:true});
      $('#recOut').innerHTML = candTable(r.recommendations);
      $('#recStatus').textContent = `saved as run #${r.run_id}`;
    }
  } catch(err){ $('#recStatus').textContent = 'failed: '+err.message; }
  b.disabled = false;
};

function candTable(list){
  if (!list || !list.length) return '<div class="empty">No candidates.</div>';
  return `<table><thead><tr><th>Take</th><th>Pos</th><th class="num">Proj</th>
    <th class="num">VOR</th><th class="num">ADP</th><th class="num">Season pts</th>
    <th class="num">Range</th><th class="num">Cost</th></tr></thead><tbody>` +
    list.map((x,i)=>`<tr class="${i===0?'me':''}">
      <td><b>${x.name}</b>${x.flag?` <span class="flagq">${x.flag}</span>`:''}</td>
      <td><span class="pill ${posCls(x.pos)}">${x.pos}</span></td>
      <td class="num">${fmt(x.proj)}</td><td class="num">${fmt(x.vor)}</td>
      <td class="num">${x.adp>900?'—':fmt(x.adp)}</td>
      <td class="num"><b>${fmt(x.mean)}</b></td>
      <td class="num dim">${fmt(x.p10,0)}–${fmt(x.p90,0)}</td>
      <td class="num ${x.cost_vs_best<0?'bad':'good'}">${x.cost_vs_best===0?'best':fmt(x.cost_vs_best)}</td>
    </tr>`).join('') + '</tbody></table>';
}

function renderAdvice(a){
  const md = t => t.replace(/\*\*(.+?)\*\*/g,'<b>$1</b>');
  $('#adviceOut').innerHTML =
    `<div class="stat" style="border-left:3px solid var(--accent)">
       ${a.reasoning.map(r=>`<div style="margin:5px 0">${md(r)}</div>`).join('')}
     </div>`;
  const rows = a.positions.map(p=>{
    const capped = p.urgency < -900;
    return `<tr class="${(!capped && p===a.positions[0])?'me':''}">
      <td><span class="pill ${posCls(p.pos)}">${p.pos}</span></td>
      <td>${p.best_by_rollout || p.best_available}</td>
      <td class="num">${p.rollout_mean?fmt(p.rollout_mean,0):'—'}</td>
      <td class="num ${capped?'dim':(p.urgency<0?'bad':'good')}">
        ${capped?'not needed':(p.urgency===0?'best':fmt(p.urgency))}</td>
      <td class="num ${p.cost_of_waiting>=15?'bad':''}">${fmt(p.cost_of_waiting,0)}</td>
      <td class="num">${p.count_above_replacement}</td>
      <td class="num">${p.p_best_survives==null?'—':Math.round(p.p_best_survives*100)+'%'}</td>
      <td class="num dim">${p.tier_left}</td></tr>`;
  }).join('');
  const hot = Object.entries(a.runs||{}).filter(([,r])=>r.hot).map(([k])=>k);
  $('#posBoard').innerHTML = `<h2 style="font-size:13px;color:var(--dim);
      text-transform:uppercase;letter-spacing:.8px;margin:0 0 8px">Position board</h2>
    <table><thead><tr><th>Pos</th><th>Best option</th><th class="num">Season pts</th>
      <th class="num">vs best</th><th class="num">Decay by next pick</th>
      <th class="num">Left over repl.</th><th class="num">Survives</th>
      <th class="num">In tier</th></tr></thead><tbody>${rows}</tbody></table>
    <div class="hint"><b>Decay</b> is how much the best player at that position is expected to
      fall between now and your next pick — high decay means take it now.
      <b>Survives</b> is how often the top name there is still on the board when you pick again.
      ${hot.length?`<br><b class="bad">Run alert:</b> ${hot.join(', ')} going fast.`:''}</div>`;
  $('#recOut').innerHTML = candTable(a.recommendations);
}

/* ------------------------------------------------------------- strategies */
let STRATS = null;
async function loadStrategies(){
  const slot = mySlot() || 1;
  const sel = $('#stPick');
  if (!sel.options.length){
    sel.innerHTML = '<option value="">— choose a strategy —</option>';
  }
  STRATS = await api('/api/strategies?my_slot='+slot);
  const keep = sel.value;
  sel.innerHTML = '<option value="">— choose a strategy —</option>' +
    STRATS.strategies.map(s=>`<option value="${s.key}">${s.name}</option>`).join('');
  if (keep) { sel.value = keep; renderPlan(); }
}
$('#stPick').onchange = renderPlan;
function renderPlan(){
  const k = $('#stPick').value;
  const s = (STRATS?.strategies||[]).find(x=>x.key===k);
  if (!s){ $('#stPlan').innerHTML=''; return; }
  $('#stPlan').innerHTML = `<div class="stat" style="margin-bottom:12px">
      <b>${s.name}</b> — ${s.blurb}<div class="sub" style="margin-top:6px">${s.rationale}</div></div>
    <table><thead><tr><th>Round</th><th class="num">Your pick</th><th>Target</th></tr></thead><tbody>` +
    s.plan.map(r=>`<tr><td class="tag">R${r.round}</td><td class="num">#${r.pick}</td>
      <td>${r.target}</td></tr>`).join('') + '</tbody></table>';
}
$('#stBtn').onclick = async e => {
  const slot = mySlot(); if(!slot) return alert('Set your draft slot first.');
  e.target.disabled = true;
  const mode = $('#stMode').value;
  $('#stStatus').innerHTML = '<span class="spin"></span>' +
    (mode==='season' ? 'playing ~15,000 seasons…' : 'drafting…');
  try{
    const r = await post('/api/backtest-strategies',
      {my_slot:slot, mode, n_sims:120, n_drafts:20, n_seasons:80, save:true});
    $('#stOut').innerHTML = mode==='season' ? seasonStrat(r.results) : pointStrat(r.results);
    $('#stStatus').textContent = `saved as run #${r.run_id}`;
  }catch(err){ $('#stStatus').textContent='failed: '+err.message; }
  e.target.disabled=false;
};
function seasonStrat(res){
  const max = Math.max(...res.map(r=>r.title_odds));
  return `<table><thead><tr><th>Strategy</th><th class="num">Title odds</th>
    <th class="num">Playoffs</th><th class="num">Wins</th><th class="num">Spread</th>
    <th style="width:120px"></th></tr></thead><tbody>` +
    res.map((r,i)=>`<tr class="${i===0?'me':''}">
      <td><b>${r.name}</b><div class="sub">${r.blurb}</div></td>
      <td class="num"><b>${(r.title_odds*100).toFixed(1)}%</b></td>
      <td class="num">${(r.playoff_odds*100).toFixed(0)}%</td>
      <td class="num">${r.exp_wins}</td>
      <td class="num dim">${(r.worst_draft_title*100).toFixed(0)}–${(r.best_draft_title*100).toFixed(0)}%</td>
      <td><div class="bar"><i style="width:${100*r.title_odds/max}%"></i></div></td>
    </tr>`).join('') + `</tbody></table>
    <div class="hint">Baseline for a 10-team league is 10%. The <b>spread</b> column is the range
      across individual drafts within the same strategy — it is wide, which is the real lesson:
      which players you actually land matters more than the label on the plan.</div>`;
}
function pointStrat(res){
  return `<table><thead><tr><th>Strategy</th><th class="num">Mean lineup</th>
    <th class="num">p10–p90</th><th class="num">vs best</th><th>Typical open</th>
    </tr></thead><tbody>` + res.map((r,i)=>`<tr class="${i===0?'me':''}">
      <td><b>${r.name}</b><div class="sub">${r.blurb}</div></td>
      <td class="num"><b>${r.mean}</b></td>
      <td class="num dim">${r.p10}–${r.p90}</td>
      <td class="num ${r.vs_best<0?'bad':'good'}">${r.vs_best===0?'best':r.vs_best}</td>
      <td class="tag">${r.typical_open} <span class="dim">${r.open_pct}%</span></td>
    </tr>`).join('') + '</tbody></table>';
}

/* ------------------------------------------------------------- players tab */
let sortKey='vor', sortDir=-1;
$$('#tab-players th[data-s]').forEach(th => th.onclick = () => {
  const k = th.dataset.s;
  sortDir = (k===sortKey) ? -sortDir : -1; sortKey = k; renderPlayers();
});
$('#psearch').oninput = renderPlayers; $('#ppos').onchange = renderPlayers;
function renderPlayers(){
  const q = norm($('#psearch').value), pf = $('#ppos').value;
  const rows = PLAYERS.filter(p => (!pf||p.pos===pf) && (!q||norm(p.name).includes(q)))
    .sort((a,b)=>{
      const x=a[sortKey], y=b[sortKey];
      if (typeof x === 'string') return sortDir * (x>y?1:-1);
      return sortDir * ((x??-1e9)-(y??-1e9));
    }).slice(0,400);
  $('#playersBody').innerHTML = rows.map(p=>`<tr>
    <td>${p.name}</td><td><span class="pill ${posCls(p.pos)}">${p.pos}${p.pos_rank||''}</span></td>
    <td class="num">${p.tier||'—'}</td><td class="num">${fmt(p.proj)}</td>
    <td class="num">${fmt(p.vor)}</td><td class="num">${p.adp>900?'—':fmt(p.adp)}</td>
    <td class="num ${p.proj_spread>35?'bad':''}">${fmt(p.proj_spread)}</td>
    <td class="num">${fmt(p.week_sd)}</td><td class="num dim">${fmt(p.actual_2025,0)}</td>
    <td class="num">${p.bye||'—'}</td><td>${flagHtml(p)}</td></tr>`).join('');
}

/* ------------------------------------------------------------- alerts */
async function loadFlags(){
  const d = await api('/api/flags');
  $('#flagsBody').innerHTML = d.flagged.map(p=>`<tr>
    <td>${p.name}</td><td><span class="pill ${posCls(p.pos)}">${p.pos}</span></td>
    <td class="num">${p.adp>900?'—':fmt(p.adp)}</td>
    <td>${flagHtml(p)||'<span class="dim">—</span>'}</td>
    <td class="num">${p.games_missed||0}</td><td class="dim">${p.note||''}</td>
    <td><button onclick="fillOv(${JSON.stringify(p.name).replace(/"/g,'&quot;')})">Set games</button></td>
  </tr>`).join('') || '<tr><td colspan="7" class="empty">Nobody flagged.</td></tr>';
}
window.fillOv = n => { $('#ovName').value = n; $('#ovGames').focus(); };
$('#ovSave').onclick = async e => {
  const name = $('#ovName').value.trim(); if(!name) return;
  e.target.disabled = true;
  try {
    await post('/api/overrides', {name, games_missed:Number($('#ovGames').value),
                                  note:$('#ovNote').value});
    PLAYERS = (await api('/api/players?limit=700')).players;
    renderAll(); loadFlags();
    $('#ovName').value=''; $('#ovNote').value=''; $('#ovGames').value=0;
  } catch(err){ alert(err.message); }
  e.target.disabled = false;
};

/* ------------------------------------------------------------- draft sim */
$('#dsBtn').onclick = async e => {
  const slot = mySlot(); if(!slot) return alert('Set your draft slot first.');
  e.target.disabled = true; $('#dsStatus').innerHTML='<span class="spin"></span>drafting…';
  try{
    const r = await post('/api/simulate-draft', {my_slot:slot, n_sims:Number($('#dsSims').value),
                                                 label:$('#dsLabel').value||null, save:true});
    $('#dsOut').innerHTML = `
      <div class="grid g3" style="margin-bottom:14px">
        <div class="stat"><div class="lbl">Mean starting lineup</div><div class="big">${r.mean_starting_points}</div></div>
        <div class="stat"><div class="lbl">10th–90th percentile</div><div class="big">${r.p10}–${r.p90}</div></div>
        <div class="stat"><div class="lbl">Drafts simulated</div><div class="big">${r.n_sims}</div></div>
      </div>
      <h2>Who you end up with, by pick</h2>
      <table><thead><tr><th>Pick</th><th>Most likely selections</th></tr></thead><tbody>` +
      Object.entries(r.pick_frequency).map(([pk,opts])=>`<tr><td class="tag">#${pk}</td><td>` +
        opts.map(([n,c])=>`${n} <span class="dim">${Math.round(100*c/r.n_sims)}%</span>`).join(' · ') +
      `</td></tr>`).join('') + '</tbody></table>';
    $('#dsStatus').textContent = `saved as run #${r.run_id}`;
  }catch(err){ $('#dsStatus').textContent='failed: '+err.message; }
  e.target.disabled=false;
};

/* ------------------------------------------------------------- season sim */
$('#ssBtn').onclick = async e => {
  const slot = mySlot(); if(!slot) return alert('Set your draft slot first.');
  e.target.disabled = true; $('#ssStatus').innerHTML='<span class="spin"></span>simulating seasons…';
  try{
    const r = await post('/api/simulate-season', {
      my_slot:slot, my_roster: $('#ssUseMine').checked ? STATE.my_roster : [],
      n_sims:Number($('#ssSims').value), label:$('#ssLabel').value||null, save:true});
    $('#ssOut').innerHTML = renderSeason(r);
    $('#ssStatus').textContent = `saved as run #${r.run_id}`;
  }catch(err){ $('#ssStatus').textContent='failed: '+err.message; }
  e.target.disabled=false;
};
function renderSeason(r){
  const me = r.teams.find(t=>t.is_me) || {};
  const max = Math.max(...r.teams.map(t=>t.title_odds));
  return `<div class="grid g3" style="margin-bottom:14px">
    <div class="stat"><div class="lbl">My title odds</div><div class="big">${(me.title_odds*100).toFixed(1)}%</div>
      <div class="sub">baseline 10%</div></div>
    <div class="stat"><div class="lbl">My playoff odds</div><div class="big">${(me.playoff_odds*100).toFixed(1)}%</div>
      <div class="sub">expected record ${me.exp_wins}–${(14-me.exp_wins).toFixed(1)}</div></div>
    <div class="stat"><div class="lbl">My weekly score</div><div class="big">${r.my_weekly.mean}</div>
      <div class="sub">${r.my_weekly.p10}–${r.my_weekly.p90} typical range</div></div>
  </div>
  <table><thead><tr><th>Team</th><th>Div</th><th class="num">Wins</th><th class="num">Points</th>
    <th class="num">Playoffs</th><th class="num">Title</th><th style="width:130px"></th>
  </tr></thead><tbody>` + r.teams.map(t=>`<tr class="${t.is_me?'me':''}">
    <td>${t.is_me?'<b>':''}${t.team}${t.is_me?'</b>':''}</td><td class="dim">${t.division||''}</td>
    <td class="num">${t.exp_wins}</td><td class="num">${t.exp_points}</td>
    <td class="num">${(t.playoff_odds*100).toFixed(0)}%</td>
    <td class="num"><b>${(t.title_odds*100).toFixed(1)}%</b></td>
    <td><div class="bar"><i style="width:${max?100*t.title_odds/max:0}%"></i></div></td>
  </tr>`).join('') + `</tbody></table>
  ${r.my_lineup ? `<div class="card" style="margin-top:14px"><h2>My projected starters</h2>
   <table><tbody>${r.my_lineup.map(([n,p,v])=>`<tr><td><span class="pill ${posCls(p)}">${p}</span></td>
   <td>${n}</td><td class="num">${v}</td></tr>`).join('')}</tbody></table></div>`:''}
  <div class="hint">The other nine teams are drafted by the ADP model, so these odds measure your
    roster against <i>simulated</i> opponents. Treat them as directional — real leaguemates are not
    uniform ADP followers.</div>`;
}

/* ------------------------------------------------------------- runs */
async function loadRuns(){
  const d = await api('/api/runs');
  $('#runsBody').innerHTML = d.runs.map(r=>`<tr>
    <td class="tag">#${r.id}</td><td class="dim">${new Date(r.created_at*1000).toLocaleString()}</td>
    <td><span class="pill K">${r.kind}</span></td><td>${r.label||''}</td>
    <td class="dim tag">${JSON.stringify(r.params).slice(0,60)}</td>
    <td style="white-space:nowrap"><button onclick="viewRun(${r.id})">View</button>
    <button class="danger" onclick="delRun(${r.id})">Delete</button></td></tr>`).join('')
    || '<tr><td colspan="6" class="empty">Nothing saved yet.</td></tr>';
}
window.viewRun = async id => {
  const r = await api('/api/runs/'+id);
  $('#runDetail').innerHTML = `<div class="card"><h2>Run #${id} — ${r.kind}</h2>` +
    (r.kind==='season' ? renderSeason(r.result)
     : `<pre class="tag" style="white-space:pre-wrap;overflow:auto;max-height:460px">${
        JSON.stringify(r.result,null,1)}</pre>`) + '</div>';
  $('#runDetail').scrollIntoView({behavior:'smooth'});
};
window.delRun = async id => {
  if(!confirm('Delete run #'+id+'?')) return;
  await fetch('/api/runs/'+id,{method:'DELETE'}); loadRuns(); $('#runDetail').innerHTML='';
};

window.draft = draft;
boot().catch(e => document.body.insertAdjacentHTML('afterbegin',
  `<div style="padding:20px;color:#f85149">Failed to start: ${e.message}</div>`));
