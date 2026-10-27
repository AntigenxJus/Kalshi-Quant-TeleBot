const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(n);
const cents=n=>n==null?'—':(n*100).toFixed(1)+'¢';
const percent=n=>n==null?'—':(n*100).toFixed(1)+'%';
let current=null;
function render(d){
 current=d;
 $('pnl').textContent=money(d.pnl);$('pnl').className='value '+(d.pnl<0?'negative':'positive');
 $('today').textContent=`Today ${money(d.day_pnl)} · ${d.day} ET`;
 $('exposure').textContent=money(d.exposure);$('risk').textContent=money(d.risk_used);
 $('risk-bar').style.width=Math.min(100,d.risk_used/500*100)+'%';$('settled').textContent=d.settled;
 $('winrate').textContent=d.settled?`${percent(d.wins/d.settled)} profitable trades · after costs`:'No settled fills yet';
 $('toggle').textContent=d.running?'Pause paper entries':'Resume paper entries';$('toggle').className=d.running?'':'primary';
 const age=d.updated?Math.floor(Date.now()/1000-d.updated):null;
 $('connection').textContent=`${d.running?'AUTO PAPER':'PAUSED'} · ${d.health}${age==null?'':` · checked ${age}s ago`}`;
 $('refresh').textContent=age!=null&&age>90?'Collector may be stale':'15-second polling';
 $('markets').innerHTML=d.cards.map(c=>{
  const v=c.view;const left=c.close?Math.max(0,Math.floor(c.close-Date.now()/1000)):0;
  return `<article class="market"><div class="market-head"><h3>${esc(c.name)}</h3><span class="pill">${esc(c.status)}</span></div><div class="quotes"><div><div class="label">YES ask</div><div class="quote">${cents(v?.ask)}</div></div><div><div class="label">NO ask</div><div class="quote">${cents(v?.no_ask)}</div></div><div><div class="label">Time left</div><div class="quote">${c.close?Math.floor(left/60)+':'+String(left%60).padStart(2,'0'):'—'}</div></div></div><div class="hint">${c.prediction==null?'Model warming up':`Sample-time YES estimate ${percent(c.prediction)} · experimental`}</div><p class="reason">${esc(c.reason)}</p>${c.rules?`<details><summary>Settlement rule &amp; source</summary><p>${esc(c.rules)}</p><p>${esc(c.ticker)} · quote received ${new Date(c.received*1000).toLocaleTimeString()}</p></details>`:''}</article>`;
 }).join('')||'<div class="market subtle">No quotes yet. Check the collector status above.</div>';
 $('models').innerHTML=d.models.map(m=>`<div class="model"><div class="row"><span>${esc(m.name)}</span><span class="hint">${m.trained?'Frozen · forward test':`${m.labels} / ${m.target}`}</span></div><div class="progress"><span style="width:${Math.min(100,m.labels/m.target*100)}%"></span></div></div>`).join('');
 const ready=d.models.filter(m=>m.trained).length;
 $('assistant').textContent=d.risk_used>=400?'The daily risk stop is active. Collection and settlement checks continue; new simulated entries are blocked.':!d.running?'Paper entries are paused. The collector continues to record quotes and resolve existing observations.':!ready?'The models are warming up. No simulated entries will be made until the first 100 windows per commodity are finalized. Zero trades is expected during this stage.':`${ready} model(s) are testing future windows with simulated funds. Estimates remain unvalidated. Skipped windows are recorded alongside fills; no daily profit quota is enforced.`;
 $('scored').textContent=d.scored;$('brier').textContent=d.brier==null?'— / —':`${d.brier.toFixed(3)} / ${d.baseline_brier.toFixed(3)}`;
 $('drawdown').textContent=`Max realized drawdown ${money(d.max_drawdown)}`;
 if(d.curve.length<2){$('chart').innerHTML='<div class="chart-empty">The equity curve starts after the first finalized paper trade.</div>'}
 else {const a=d.curve.map(p=>p.value),lo=Math.min(...a)-5,hi=Math.max(...a)+5;const points=a.map((v,i)=>`${15+i/(a.length-1)*670},${140-(v-lo)/(hi-lo)*120}`).join(' ');$('chart').innerHTML=`<svg class="chart" viewBox="0 0 700 160" role="img" aria-label="Realized paper equity"><line x1="15" x2="685" y1="140" y2="140" stroke="#344451"/><polyline points="${points}" fill="none" stroke="#75ebc4" stroke-width="2"/></svg>`}
 $('ledger').innerHTML=d.trades.length?'<table><thead><tr><th>Market</th><th>Side / size</th><th>Ask</th><th>Max loss incl. costs</th><th>Status</th><th>Net P&amp;L</th></tr></thead><tbody>'+d.trades.map(t=>`<tr><td>${esc(t.ticker)}</td><td>${esc(t.side.toUpperCase())} × ${t.quantity}</td><td>${cents(t.price)}</td><td>${money(t.cost)}</td><td>${esc(t.status)}</td><td class="${t.pnl<0?'negative':'positive'}">${t.pnl==null?'—':money(t.pnl)}</td></tr>`).join('')+'</tbody></table>':'<div class="empty">No paper fills. Observations accumulate automatically while the collector runs.</div>';
 $('logs').innerHTML=d.logs.map(l=>`<div class="log">${new Date(l.ts*1000).toLocaleTimeString()} · ${esc(l.text)}</div>`).join('')||'<p class="hint">Collector started. Waiting for model or trade events.</p>';
}
async function refresh(){try{const r=await fetch('/api/state');if(!r.ok)throw Error(`HTTP ${r.status}`);render(await r.json());$('error').style.display='none'}catch(e){$('error').style.display='block';$('error').textContent='Dashboard disconnected. Values below may be stale. Restart the local server or Codespace. '+e.message}}
let busy=false;
$('toggle').onclick=async()=>{if(!current||busy)return;busy=true;$('toggle').disabled=true;try{const r=await fetch(current.running?'/api/pause':'/api/start',{method:'POST',headers:{'X-Paper-Token':document.querySelector('meta[name="paper-token"]').content}});if(!r.ok)throw Error(`HTTP ${r.status}`);await refresh()}catch(e){$('error').textContent=e.message;$('error').style.display='block'}finally{busy=false;$('toggle').disabled=false}};
refresh();setInterval(refresh,5000);
