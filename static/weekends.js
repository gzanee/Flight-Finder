'use strict';
const $ = id => document.getElementById(id);
const days = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'];
let jobId = null, pollTimer = null, offset = 0, prepared = null, rendering = '';
let restorePending = true, rechecking = false, pollSequence = 0;
const money = n => new Intl.NumberFormat('it-IT', {style:'currency',currency:'EUR',maximumFractionDigits:2}).format(n);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const duration = minutes => `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;
const countLabel = (n, single, plural) => `${n} ${n === 1 ? single : plural}`;
const nightsLabel = spec => spec.min_nights === spec.max_nights ? `${spec.min_nights} notti` : `${spec.min_nights}–${spec.max_nights} notti`;
// Preserve airport-local date/time, rather than converting to the browser's timezone.
const localTime = iso => iso.slice(11,16);
const localDay = iso => new Date(iso.slice(0,10) + 'T12:00:00').toLocaleDateString('it-IT', {weekday:'short',day:'numeric',month:'short',year:'numeric'});
const dateISO = value => `${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,'0')}-${String(value.getDate()).padStart(2,'0')}`;
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
function invalidatePlan() {
  prepared = null;
  $('start-search')?.setAttribute('disabled','');
}
function safeLink(url) {
  try { const u = new URL(url); return u.protocol === 'https:' && u.hostname === 'www.google.com' ? u.href : '#'; }
  catch { return '#'; }
}
async function api(path, method='GET', body) {
  const response = await fetch(path, {method, headers:{'Content-Type':'application/json'},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) {
    const failure = new Error(data.error || 'Richiesta non riuscita.');
    failure.status = response.status;
    throw failure;
  }
  return data;
}
for (const [id, selected] of [['departure-days', 4], ['return-days', 6]]) {
  $(id).innerHTML = days.map((d,i) => `<label><input type="checkbox" value="${i}" ${i === selected ? 'checked' : ''}>${d}</label>`).join('');
}
const today = new Date(), end = new Date(today); end.setMonth(end.getMonth() + 3);
$('start-date').value = dateISO(today); $('start-date').min = dateISO(today);
$('end-date').value = dateISO(end); $('end-date').min = dateISO(today);
function setMode(weekend) {
  for (const [id, selected] of [['weekend-mode',weekend],['flexible-mode',!weekend]]) {
    $(id).classList.toggle('selected', selected); $(id).setAttribute('aria-pressed', String(selected));
  }
  if (weekend) {
    for (const [id, day] of [['departure-days',4],['return-days',6]]) {
      $(id).querySelectorAll('input').forEach(el => el.checked = Number(el.value) === day);
    }
    $('min-nights').value = '2'; $('max-nights').value = '2';
    $('departure-min').value = '18:00'; $('return-min').value = '18:00';
    $('departure-max').value = '23:59'; $('return-max').value = '23:59';
  }
  invalidatePlan();
}
$('weekend-mode').onclick = () => setMode(true);
$('flexible-mode').onclick = () => setMode(false);
$('destination-mode').onchange = () => {
  $('custom-field').hidden = $('destination-mode').value !== 'custom'; invalidatePlan();
};
$('trip-form').addEventListener('input', invalidatePlan);
function restoreForm(spec, plan) {
  const weekend = spec.departure_days.join() === '4' && spec.return_days.join() === '6'
    && spec.min_nights === 2 && spec.max_nights === 2;
  setMode(weekend);
  document.querySelectorAll('[name=origin]').forEach(el => el.checked = spec.origins.includes(el.value));
  for (const key of ['start_date','end_date','departure_min','departure_max','return_min','return_max',
    'min_nights','max_nights','budget','adults','children','max_stops','return_airport','baggage','airline_mode']) {
    $(key.replaceAll('_','-')).value = spec[key];
  }
  for (const [id,key] of [['departure-days','departure_days'],['return-days','return_days']]) {
    $(id).querySelectorAll('input').forEach(el => el.checked = spec[key].includes(Number(el.value)));
  }
  $('self-transfer').checked = spec.allow_self_transfer;
  $('final-arrival').value = spec.final_arrival_max || '';
  $('min-hours').value = spec.min_destination_hours;
  $('destination-mode').value = spec.destinations.length ? 'custom' : 'anywhere';
  $('custom-field').hidden = !spec.destinations.length;
  $('destinations').value = spec.destinations.join(', ');
  $('plan-panel').innerHTML = `<p class="eyebrow">RICERCA RECUPERATA</p>
    <h2>${countLabel(plan.date_pairs.length, 'combinazione di date', 'combinazioni di date')}<br>${countLabel(plan.candidate_count, 'destinazione candidata', 'destinazioni candidate')}</h2>
    <p>${escapeHTML(spec.origins.join(' + '))} · ${nightsLabel(spec)} · budget ${money(spec.budget)} A/R.</p>
    <p class="hint">I filtri e i risultati dell’ultima ricerca sono stati ripristinati. Per una nuova ricerca, modifica i filtri e ricalcola le combinazioni.</p>`;
}
function values() {
  const num = id => Number($(id).value);
  const spec = {
    origins: [...document.querySelectorAll('[name=origin]:checked')].map(el => el.value),
    start_date: $('start-date').value, end_date: $('end-date').value,
    departure_days: [...$('departure-days').querySelectorAll('input:checked')].map(el => Number(el.value)),
    return_days: [...$('return-days').querySelectorAll('input:checked')].map(el => Number(el.value)),
    departure_min: $('departure-min').value, departure_max: $('departure-max').value,
    return_min: $('return-min').value, return_max: $('return-max').value,
    min_nights:num('min-nights'), max_nights:num('max-nights'), budget:num('budget'),
    adults:num('adults'), children:num('children'), max_stops:num('max-stops'),
    return_airport:$('return-airport').value, baggage:$('baggage').value,
    airline_mode:$('airline-mode').value, allow_self_transfer:$('self-transfer').checked,
    final_arrival_max:$('final-arrival').value || null, min_destination_hours:num('min-hours')
  };
  if ($('destination-mode').value === 'custom') {
    if (!$('destinations').value.trim()) throw new Error('Inserisci almeno un aeroporto di destinazione.');
    spec.destinations = $('destinations').value.trim();
  }
  return spec;
}
$('trip-form').onsubmit = async event => {
  event.preventDefault(); error(''); $('plan-button').disabled = true;
  try {
    const body = values();
    const data = await api('/api/flexible/plan', 'POST', body);
    if (JSON.stringify(body) !== JSON.stringify(values())) {
      return error('I filtri sono cambiati durante il calcolo: ricalcola le combinazioni.');
    }
    prepared = body;
    const plan = data.plan;
    $('plan-panel').innerHTML = `<p class="eyebrow">RICERCA PRONTA</p>
      <h2>${countLabel(plan.date_pairs.length, 'combinazione di date', 'combinazioni di date')}<br>${countLabel(plan.candidate_count, 'destinazione candidata', 'destinazioni candidate')}</h2>
      <p>Da ${escapeHTML(body.origins.join(' + '))}, fino a <strong>${money(body.budget)} A/R</strong> per ${countLabel(body.adults + body.children, 'viaggiatore', 'viaggiatori')}.</p>
      <ul class="date-pairs">${plan.date_pairs.map(([a,b]) => `<li>${localDay(a)} → ${localDay(b)}</li>`).join('')}</ul>
      <div class="rule"></div><p>Fino a <b>${plan.searches_upper_bound}</b> controlli delle singole tratte per una scansione completa. Se servono tutti, circa <b>${countLabel(Math.ceil(plan.estimated_minutes), 'minuto', 'minuti')}</b> al ritmo previsto, più i tempi di rete.</p>
      <p class="hint">Le tratte ripetute riusano prezzi recenti. Se Google limita le richieste, ci fermiamo e conserviamo il progresso.</p>
      <p class="hint">${escapeHTML(plan.coverage)}.</p>
      <button id="start-search" class="primary wide" type="button">Avvia ricerca A/R →</button>`;
    $('start-search').onclick = start;
  } catch (e) { error(e.message); }
  finally { $('plan-button').disabled = false; }
};
async function start() {
  if (rechecking) return error('Attendi che il ricontrollo dei voli sia terminato.');
  if (!prepared) return error('I filtri sono cambiati: ricalcola le combinazioni.');
  $('start-search').disabled = true;
  try {
    const data = await api('/api/flexible/jobs','POST',prepared);
    jobId = data.id; offset = 0; rendering = ''; restorePending = false;
    $('result-feedback').textContent = '';
    try { localStorage.setItem('flight-finder-job',jobId); } catch { /* storage optional */ }
    await poll(); $('progress-panel').scrollIntoView({behavior:'smooth'});
  } catch(e) { error(e.message); $('start-search').disabled = false; }
}
const states = {running:'Cerchiamo il tuo viaggio',cancelling:'Arresto dopo la richiesta in corso…',cancelled:'Ricerca fermata',blocked:'Google ha limitato la ricerca',interrupted:'Ricerca da riprendere',complete:'Controllo dei candidati completato'};
async function poll() {
  clearTimeout(pollTimer);
  if (!jobId || rechecking) return;
  const sequence = ++pollSequence, currentJobId = jobId;
  try {
    const job = await api(`/api/flexible/jobs/${jobId}?sort=${$('result-sort').value}&offset=${offset}`);
    if (sequence !== pollSequence || currentJobId !== jobId || rechecking) return;
    if (restorePending) { restorePending = false; restoreForm(job.request, job.plan); }
    $('progress-panel').hidden = false; $('results-panel').hidden = false;
    $('status-label').textContent = states[job.status] || job.status;
    $('progress').max = job.total || 1; $('progress').value = job.completed;
    $('progress-text').textContent = `${job.completed} / ${job.total} combinazioni esaminate · ${job.result_count} coppie A/R trovate`;
    $('current').textContent = job.current || '';
    $('job-summary').textContent = `${job.request.origins.join(' + ')} · ${localDay(job.request.start_date)} → ${localDay(job.request.end_date)} · budget ${money(job.request.budget)} A/R · ${nightsLabel(job.request)}`;
    $('issues').textContent = job.issues.join(' '); $('issues').hidden = !job.issues.length;
    $('cancel').hidden = !['running','cancelling'].includes(job.status);
    $('cancel').disabled = job.status === 'cancelling';
    $('resume').hidden = !['cancelled','blocked','interrupted'].includes(job.status);
    $('results-heading').textContent = `${job.result_count} viaggi con andata e ritorno`;
    $('result-note').textContent = `${job.plan.coverage}. ${job.unverified} righe senza dati sufficienti escluse. ${job.status === 'complete' ? '' : 'Risultati parziali. '}Gli orari sono locali agli aeroporti. I prezzi di oltre 5 minuti richiedono un nuovo controllo.`;
    const version = `${job.updated_at}|${offset}|${$('result-sort').value}|${job.results.map(x=>x.needs_recheck).join(',')}`;
    if (rendering !== version) {
      rendering = version;
      $('results').innerHTML = job.results.length ? job.results.map(card).join('') :
        '<div class="panel"><p>Nessuna coppia completa confermata finora con questi filtri.</p><p class="hint">Una ricerca incompleta o bloccata non dimostra che non esistano voli. Le rotte senza un prezzo live restano fuori dai risultati.</p></div>';
    }
    $('prev-page').disabled = offset === 0;
    $('next-page').disabled = !job.has_more;
    $('page-label').textContent = job.result_count ? `${offset+1}–${offset+job.results.length} di ${job.result_count}` : '0 risultati';
    if (['running','cancelling'].includes(job.status)) pollTimer = setTimeout(poll,2500);
    else if (job.status === 'complete') pollTimer = setTimeout(poll,30000);
  } catch(e) {
    if (sequence !== pollSequence || currentJobId !== jobId) return;
    error(e.message);
    if (e.status === 404) {
      jobId = null; restorePending = false;
      try { localStorage.removeItem('flight-finder-job'); } catch { /* storage optional */ }
      return;
    }
    pollTimer = setTimeout(poll, 10000);
  }
}
function leg(quote, title) {
  const first = quote.segments[0], last = quote.segments.at(-1);
  return `<div class="leg"><span class="leg-label">${title}</span><div><strong>${localTime(first.departure)} ${escapeHTML(first.from_code)} → ${localTime(last.arrival)} ${escapeHTML(last.to_code)}</strong>
    <p class="hint">${localDay(first.departure)} → arrivo ${localDay(last.arrival)} · ${duration(quote.duration_min)} · ${quote.segments.length === 1 ? 'diretto' : `${quote.segments.length-1} scalo`}</p>
    <p class="hint">${quote.segments.map(s=>`${escapeHTML(s.airline_name)} · ${escapeHTML(s.airline_code)} ${escapeHTML(s.flight_number)}`).join(' / ')}</p>
    ${quote.segments.length > 1 ? `<p class="hint">${quote.self_transfer === true ? 'Self-transfer' : quote.self_transfer === false ? 'Scalo protetto indicato da Google' : 'Protezione dello scalo non verificata'}</p>` : ''}
    ${quote.segments.length > 1 ? `<details><summary>Tratte e scali</summary>${quote.segments.map(s=>`<p class="hint">${escapeHTML(s.from_code)} ${localDay(s.departure)} ${localTime(s.departure)} → ${escapeHTML(s.to_code)} ${localDay(s.arrival)} ${localTime(s.arrival)}</p>`).join('')}</details>` : ''}</div></div>`;
}
function card(pair) {
  const origin = pair.outbound.segments[0].from_code, dest = pair.outbound.segments.at(-1).to_code;
  const home = pair.inbound.segments.at(-1).to_code;
  return `<article class="trip" data-id="${escapeHTML(pair.id)}"><div class="trip-top"><div><h3>${escapeHTML(origin)} → ${escapeHTML(dest)} → ${escapeHTML(home)}</h3><p class="hint">${countLabel(pair.nights, 'notte', 'notti')} · ${countLabel(pair.outbound.travelers, 'viaggiatore', 'viaggiatori')}</p></div><div class="price">${money(pair.price)}<small>totale A/R · due biglietti</small></div></div>
    ${leg(pair.outbound,'Andata')}${leg(pair.inbound,'Ritorno')}
    <div class="facts"><span class="pill useful">${duration(pair.useful_minutes)} a destinazione</span><span class="pill">Andata ${money(pair.outbound.price)} + ritorno ${money(pair.inbound.price)}</span><span class="pill">${({base:'Tariffa base',cabin:'Filtro trolley',checked:'Filtro stiva'})[pair.outbound.baggage] || 'Bagaglio'}</span></div>
    <p class="hint" style="margin-top:12px">${pair.needs_recheck ? 'Prezzo da ricontrollare' : 'Prezzo osservato su Google'} · controllo ${new Date(pair.checked_at).toLocaleString('it-IT')}. Vendibilità finale da confermare sul sito del venditore.</p>
    <div class="trip-actions"><button type="button" class="recheck">Ricontrolla questi voli</button><a href="${escapeHTML(safeLink(pair.outbound.booking_url))}" target="_blank" rel="noopener noreferrer">Apri andata ↗</a><a href="${escapeHTML(safeLink(pair.inbound.booking_url))}" target="_blank" rel="noopener noreferrer">Apri ritorno ↗</a></div><div class="recheck-output" role="status"></div></article>`;
}
$('results').onclick = async event => {
  const button = event.target.closest('button.recheck'); if (!button) return;
  const article = button.closest('[data-id]'), output = article.querySelector('.recheck-output');
  if (rechecking) return;
  rechecking = true; clearTimeout(pollTimer);
  button.disabled = true; output.textContent = 'Controllo entrambe le tratte…';
  $('result-feedback').textContent = '';
  try {
    const data = await api(`/api/flexible/jobs/${jobId}/results/${article.dataset.id}/recheck`,'POST',{});
    if (data.status === 'confirmed') {
      $('result-feedback').textContent = data.price_changed ? `Prezzo aggiornato: ${money(data.result.price)} A/R.` : 'Stessi voli e prezzo confermati adesso su Google.';
    } else {
      $('result-feedback').textContent = data.message;
    }
  } catch(e) { $('result-feedback').textContent = `Non verificato: ${e.message}`; }
  finally {
    button.disabled = false; output.textContent = ''; rechecking = false; rendering = '';
    await poll();
  }
};
$('cancel').onclick = async () => { try { await api(`/api/flexible/jobs/${jobId}/cancel`,'POST',{}); await poll(); } catch(e){error(e.message);} };
$('resume').onclick = async () => { try { await api(`/api/flexible/jobs/${jobId}/resume`,'POST',{}); await poll(); } catch(e){error(e.message);} };
$('result-sort').onchange = () => {offset=0; poll();};
$('prev-page').onclick = () => {offset=Math.max(0,offset-50); poll();};
$('next-page').onclick = () => {offset+=50; poll();};
try { jobId = localStorage.getItem('flight-finder-job'); } catch { /* storage optional */ }
if (jobId) poll();
