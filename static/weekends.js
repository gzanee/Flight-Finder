'use strict';
const $ = id => document.getElementById(id);
const days = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'];
let jobId = null, pollTimer = null, offset = 0, prepared = null, rendering = '';
let restorePending = true, rechecking = false, pollSequence = 0;
let airportLabels = {};
let resultView = null, resultJob = null, focusResult = '', filterTimer;
const filterFields = {max_price:'filter-price',min_hours:'filter-hours',origin:'filter-origin',destination:'filter-destination',airline:'filter-airline',home_before:'filter-home'};
function resultQuery() {
  const params = new URLSearchParams({sort:$('result-sort').value,offset});
  for (const [key,id] of Object.entries(filterFields)) if ($(id).value && !$(id).disabled) params.set(key,$(id).value);
  const excluded = [...$('excluded-options').querySelectorAll('input:checked')].map(el=>el.value);
  if (excluded.length) params.set('exclude',excluded.join(','));
  if (focusResult) params.set('focus',focusResult);
  return params.toString();
}
const airportInfo = code => airportLabels[code] || {city:'Aeroporto',name:'Aeroporto '+code};
const shortDay = iso => new Date(iso.slice(0,10)+'T12:00:00').toLocaleDateString('it-IT',{weekday:'short',day:'numeric',month:'short'});
const money = n => new Intl.NumberFormat('it-IT', {style:'currency',currency:'EUR',maximumFractionDigits:2}).format(n);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const duration = minutes => `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;
const countLabel = (n, single, plural) => `${n} ${n === 1 ? single : plural}`;
const tripLabel = spec => spec.trip_type === 'one_way' ? 'sola andata' : 'A/R';
const nightsLabel = spec => spec.trip_type === 'one_way' ? 'sola andata' : spec.date_mode === 'fixed' ? 'date fisse' : spec.min_nights === spec.max_nights ? `${spec.min_nights} notti` : `${spec.min_nights}–${spec.max_nights} notti`;
const localTime = iso => iso.slice(11,16);
const localDay = iso => new Date(iso.slice(0,10) + 'T12:00:00').toLocaleDateString('it-IT', {weekday:'short',day:'numeric',month:'short',year:'numeric'});
const dateISO = value => `${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,'0')}-${String(value.getDate()).padStart(2,'0')}`;
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
function invalidatePlan() { prepared = null; $('start-search')?.setAttribute('disabled',''); }
function safeLink(url) {
  try { const u = new URL(url); return u.protocol === 'https:' && u.hostname === 'www.google.com' ? u.href : '#'; }
  catch { return '#'; }
}
async function api(path, method='GET', body) {
  const response = await fetch(path, {method, headers:{'Content-Type':'application/json'},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) { const failure = new Error(data.error || 'Richiesta non riuscita.'); failure.status = response.status; throw failure; }
  return data;
}
const selectedAirports = {
  origins: new Map([['VCE','Venezia Marco Polo'],['TSF','Treviso Antonio Canova']]), destinations: new Map()
};
function renderAirports(kind) {
  $(kind+'-chips').innerHTML = [...selectedAirports[kind]].map(([code,name]) =>
    `<button type="button" class="airport-chip" data-code="${escapeHTML(code)}" aria-label="Rimuovi ${escapeHTML(code)}"><b>${escapeHTML(code)}</b> ${escapeHTML(name === code ? '' : name)} <span aria-hidden="true">×</span></button>`).join('');
}
function addAirport(kind, airport) {
  const map = selectedAirports[kind];
  if (!map.has(airport.code) && map.size >= (kind === 'origins' ? 10 : 200)) return error('Hai raggiunto il limite di aeroporti del gruppo.');
  map.set(airport.code, airport.name || airport.code);
  renderAirports(kind); invalidatePlan();
}
for (const kind of ['origins','destinations']) {
  const input = $(kind+'-query'), suggestions = $(kind+'-suggestions');
  let timer, sequence = 0, hits = [], active = -1;
  const close = () => { sequence++; suggestions.hidden = true; input.setAttribute('aria-expanded','false'); input.removeAttribute('aria-activedescendant'); active = -1; };
  const choose = airport => { addAirport(kind,airport); input.value = ''; close(); input.focus(); };
  $(kind+'-chips').onclick = event => {
    const button = event.target.closest('[data-code]'); if (!button) return;
    selectedAirports[kind].delete(button.dataset.code); renderAirports(kind); invalidatePlan();
  };
  suggestions.onclick = event => { const button = event.target.closest('[data-index]'); if (button) choose(hits[Number(button.dataset.index)]); };
  input.oninput = () => {
    clearTimeout(timer); close(); invalidatePlan();
    const q = input.value.trim(), current = sequence;
    if (q.length < 2) return;
    timer = setTimeout(async () => {
      try {
        const data = await api('/api/airports?q='+encodeURIComponent(q));
        if (sequence !== current || input.value.trim() !== q) return;
        hits = data.airports.filter(a => !selectedAirports[kind].has(a.code));
        suggestions.innerHTML = hits.map((a,i) => `<button type="button" role="option" aria-selected="false" id="${kind}-option-${i}" data-index="${i}"><b>${escapeHTML(a.code)}</b> ${escapeHTML(a.name)}</button>`).join('');
        suggestions.hidden = !hits.length; input.setAttribute('aria-expanded',String(!!hits.length));
      } catch { close(); }
    },200);
  };
  input.onkeydown = event => {
    if (event.key === 'Escape') { close(); return; }
    if (['ArrowDown','ArrowUp'].includes(event.key) && !suggestions.hidden) {
      event.preventDefault(); active = (active + (event.key === 'ArrowDown' ? 1 : -1) + hits.length) % hits.length;
      suggestions.querySelectorAll('[role=option]').forEach((el,i) => el.setAttribute('aria-selected',String(i === active)));
      input.setAttribute('aria-activedescendant',kind+'-option-'+active); return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      if (!suggestions.hidden && active >= 0) return choose(hits[active]);
      const codes = input.value.toUpperCase().trim().split(/[\s,;]+/);
      if (codes.every(code => /^[A-Z0-9]{3}$/.test(code))) { codes.forEach(code => addAirport(kind,{code})); input.value = ''; close(); }
      else error('Scegli un aeroporto dai suggerimenti oppure inserisci i codici IATA.');
    }
  };
  document.addEventListener('click', event => { if (!$(kind+'-picker').contains(event.target)) close(); });
  renderAirports(kind);
}
document.querySelectorAll('[data-origin]').forEach(button => button.onclick = () => addAirport('origins',{code:button.dataset.origin,name:button.textContent}));
for (const [id, selected] of [['departure-days',4],['return-days',6]]) {
  $(id).innerHTML = days.map((d,i) => `<label><input type="checkbox" value="${i}" ${i === selected ? 'checked' : ''}>${d}</label>`).join('');
}
const today = new Date(), end = new Date(today); end.setMonth(end.getMonth()+1);
$('start-date').value = dateISO(today); $('start-date').min = dateISO(today);
$('end-date').value = dateISO(end); $('end-date').min = dateISO(today);
function showFields(id, show) {
  $(id).hidden = !show;
  $(id).querySelectorAll('input,select').forEach(el => el.disabled = !show);
}
function updateModes() {
  const round = $('trip-type').value === 'round_trip', flexible = $('date-mode').value === 'flexible';
  showFields('end-field',round || flexible);
  showFields('nights-fields',round && flexible);
  showFields('return-fields',round);
  showFields('return-airport-fields',round);
  showFields('return-arrival-fields',round);
  showFields('return-duration-fields',round);
  showFields('min-hours-field',round);
  showFields('departure-days',flexible);
  showFields('return-days',round && flexible);
  showFields('destinations-picker',!$('anywhere').checked);
  showFields('destination-set-field',$('anywhere').checked);
  $('return-departure').disabled = !round || $('anywhere').checked;
  if ($('anywhere').checked) $('return-departure').value = 'same';
  $('open-jaw-hint').hidden = !round;
  $('start-label').textContent = flexible ? 'Parti non prima del' : 'Data di partenza';
  $('end-label').textContent = flexible ? (round ? 'Rientra entro il' : 'Parti entro il') : 'Data di ritorno';
  $('period-hint').textContent = flexible ? (round ? 'Andata e ritorno devono rientrare nel periodo.' : 'Cerchiamo una partenza in ogni giorno selezionato del periodo.') : 'Cerchiamo esattamente le date indicate. I giorni della settimana e le notti non limitano le date fisse.';
  $('budget-label').textContent = `Budget totale ${round ? 'A/R' : 'sola andata'} (€)`;
  $('deadline-label').textContent = round ? 'Arrivo a casa nel giorno del ritorno entro le' : 'Arrivo nel giorno della partenza entro le';
}
for (const id of ['trip-type','date-mode','anywhere']) $(id).onchange = () => { updateModes(); invalidatePlan(); };
$('preset-weekend').onclick = () => {
  $('trip-type').value = 'round_trip'; $('date-mode').value = 'flexible';
  for (const [id,day] of [['departure-days',4],['return-days',6]]) $(id).querySelectorAll('input').forEach(el => el.checked = Number(el.value) === day);
  $('min-nights').value = '2'; $('max-nights').value = '2';
  for (const prefix of ['departure','return']) { $(prefix+'-min').value = '18:00'; $(prefix+'-max').value = '23:59'; }
  updateModes(); invalidatePlan();
};
$('preset-anytime').onclick = () => {
  document.querySelectorAll('.days input').forEach(el => el.checked = true);
  for (const prefix of ['departure','return']) { $(prefix+'-min').value = '00:00'; $(prefix+'-max').value = '23:59'; }
  invalidatePlan();
};
$('trip-form').addEventListener('input',invalidatePlan);
function restoreForm(spec,plan) {
  for (const kind of ['origins','destinations']) { selectedAirports[kind] = new Map(spec[kind].map(code => [code,code])); renderAirports(kind); }
  for (const key of ['start_date','end_date','departure_min','departure_max','return_min','return_max','min_nights','max_nights','budget','adults','children','max_stops','return_airport','baggage','airline_mode']) $(key.replaceAll('_','-')).value = spec[key];
  const defaults = {destination_set:'real',trip_type:'round_trip',date_mode:'flexible',return_departure:'same',seat:'economy',infants:0,departure_arrival_min:'00:00',departure_arrival_max:'23:59',return_arrival_min:'00:00',return_arrival_max:'23:59'};
  for (const [key,defaultValue] of Object.entries(defaults)) $(key.replaceAll('_','-')).value = spec[key] ?? defaultValue;
  for (const [id,key] of [['departure-days','departure_days'],['return-days','return_days']]) $(id).querySelectorAll('input').forEach(el => el.checked = spec[key].includes(Number(el.value)));
  for (const [id,key] of [['self-transfer','allow_self_transfer'],['airport-change','allow_airport_change'],['exclude-basic-economy','exclude_basic_economy'],['schengen-transfers','schengen_transfers_only']]) $(id).checked = !!spec[key];
  $('final-arrival').value = spec.final_arrival_max || ''; $('min-hours').value = spec.min_destination_hours;
  $('airlines').value = (spec.airlines || []).join(', '); $('anywhere').checked = !spec.destinations.length;
  for (const prefix of ['departure','return']) {
    $(prefix+'-duration-min').value = (spec[prefix+'_duration_min'] || 0)/60;
    const max = spec[prefix+'_duration_max'] ?? 4320;
    $(prefix+'-duration-max').value = max === 4320 ? '' : max/60;
  }
  $('min-layover').value = spec.min_layover_minutes || 0;
  $('max-layover').value = (spec.max_layover_minutes ?? 4320) === 4320 ? '' : spec.max_layover_minutes;
  if (spec.budget === 100000) $('budget').value = '';
  updateModes();
  $('plan-panel').innerHTML = `<p class="eyebrow">RICERCA RECUPERATA</p><h2>${countLabel(plan.date_pairs.length,'combinazione di date','combinazioni di date')}<br>${countLabel(plan.candidate_count,'destinazione candidata','destinazioni candidate')}</h2><p>${escapeHTML(spec.origins.join(' + '))} · ${nightsLabel(spec)} · budget ${money(spec.budget)}.</p><p class="hint">I filtri e i risultati sono stati ripristinati. Per cercare di nuovo modifica i filtri e prepara la ricerca.</p>`;
}
function values() {
  const num = (id,fallback=0) => $(id).value === '' ? fallback : Number($(id).value);
  for (const kind of ['origins','destinations']) {
    if (kind === 'destinations' && $('anywhere').checked) continue;
    const text = $(kind+'-query').value.trim();
    if (text) {
      const codes = text.toUpperCase().split(/[\s,;]+/);
      if (!codes.every(code => /^[A-Z0-9]{3}$/.test(code))) throw new Error('Seleziona gli aeroporti dai suggerimenti prima di cercare.');
      codes.forEach(code => addAirport(kind,{code})); $(kind+'-query').value = '';
    }
  }
  const round = $('trip-type').value === 'round_trip', flexible = $('date-mode').value === 'flexible';
  const spec = {
    origins:[...selectedAirports.origins.keys()],destinations:$('anywhere').checked ? [] : [...selectedAirports.destinations.keys()],
    trip_type:$('trip-type').value, date_mode:$('date-mode').value,destination_set:$('destination-set').value,
    start_date:$('start-date').value, end_date:round || flexible ? $('end-date').value : $('start-date').value,
    departure_days:[...$('departure-days').querySelectorAll('input:checked')].map(el => Number(el.value)),
    return_days:[...$('return-days').querySelectorAll('input:checked')].map(el => Number(el.value)),
    min_nights:round && flexible ? num('min-nights') : 2, max_nights:round && flexible ? num('max-nights') : 2,
    budget:num('budget',100000), adults:num('adults'),children:num('children'),infants:num('infants'),max_stops:num('max-stops'),
    return_airport:round ? $('return-airport').value : 'same',return_departure:round ? $('return-departure').value : 'same',
    baggage:$('baggage').value,seat:$('seat').value,airline_mode:$('airline-mode').value,airlines:$('airlines').value,
    allow_self_transfer:$('self-transfer').checked,allow_airport_change:$('airport-change').checked,exclude_basic_economy:$('exclude-basic-economy').checked,schengen_transfers_only:$('schengen-transfers').checked,
    final_arrival_max:$('final-arrival').value || null,min_destination_hours:round ? num('min-hours') : 0,
    min_layover_minutes:num('min-layover'),max_layover_minutes:num('max-layover',4320)
  };
  for (const prefix of ['departure','return']) {
    for (const bound of ['min','max']) {
      spec[prefix+'_'+bound] = $(prefix+'-'+bound).value;
      spec[prefix+'_arrival_'+bound] = $(prefix+'-arrival-'+bound).value || (bound === 'min' ? '00:00' : '23:59');
      spec[prefix+'_duration_'+bound] = num(prefix+'-duration-'+bound,bound === 'min' ? 0 : 72)*60;
    }
  }
  if (!round) { spec.return_min='00:00';spec.return_max='23:59';spec.return_arrival_min='00:00';spec.return_arrival_max='23:59';spec.return_duration_min=0;spec.return_duration_max=4320; }
  if (!$('anywhere').checked && !spec.destinations.length) throw new Error('Aggiungi almeno una destinazione oppure seleziona Ovunque.');
  return spec;
}
$('trip-form').onsubmit = async event => {
  event.preventDefault(); error(''); $('plan-button').disabled = true;
  try {
    const body = values(), data = await api('/api/search/plan','POST',body);
    if (JSON.stringify(body) !== JSON.stringify(values())) return error('I filtri sono cambiati durante il calcolo: prepara di nuovo la ricerca.');
    prepared = body;
    const plan = data.plan;
    $('plan-panel').innerHTML = `<p class="eyebrow">RICERCA PRONTA</p><h2>${countLabel(plan.date_pairs.length,'combinazione di date','combinazioni di date')}<br>${countLabel(plan.candidate_count,'destinazione candidata','destinazioni candidate')}</h2>
      <p>Da ${escapeHTML(body.origins.join(' + '))} · <strong>${tripLabel(body)}</strong> · ${body.budget === 100000 ? 'senza limite di budget' : 'fino a '+money(body.budget)} per ${countLabel(body.adults+body.children+body.infants,'viaggiatore','viaggiatori')}.</p>
      <ul class="date-pairs">${plan.date_pairs.slice(0,30).map(([a,b]) => `<li>${localDay(a)}${b ? ' → '+localDay(b) : ''}</li>`).join('')}</ul>${plan.date_pairs.length > 30 ? `<p class="hint">E altre ${plan.date_pairs.length-30} combinazioni, tutte incluse nella ricerca.</p>` : ''}
      <div class="rule"></div><p>Fino a <b>${countLabel(plan.searches_upper_bound,'controllo','controlli')}</b> delle tratte. Circa <b>${countLabel(Math.max(1,Math.ceil(plan.estimated_minutes)),'minuto','minuti')}</b> di attesa programmata se servono tutti, più i tempi di rete.</p><p class="hint">${escapeHTML(plan.coverage)}.</p><button id="start-search" class="primary wide" type="button">Avvia ricerca →</button>`;
    $('start-search').onclick = start;
  } catch(e) { error(e.message); } finally { $('plan-button').disabled = false; }
};
updateModes();
async function start() {
  if (rechecking) return error('Attendi che il ricontrollo dei voli sia terminato.');
  if (!prepared) return error('I filtri sono cambiati: ricalcola le combinazioni.');
  $('start-search').disabled = true;
  try {
    const data = await api('/api/search/jobs','POST',prepared);
    jobId = data.id; offset = 0; rendering = ''; restorePending = false;
    resetResultFilters(); resultView = null; focusResult = '';
    $('result-feedback').textContent = '';
    try { localStorage.setItem('flight-finder-job',jobId); } catch { /* storage optional */ }
    await poll(); $('progress-panel').scrollIntoView({behavior:'smooth'});
  } catch(e) { error(e.message); $('start-search').disabled = false; }
}
const states = {running:'Cerchiamo il tuo viaggio',cancelling:'Arresto dopo la richiesta in corso…',cancelled:'Ricerca fermata',blocked:'Google ha limitato la ricerca',interrupted:'Ricerca da riprendere',complete:'Controllo dei candidati completato'};
async function poll() {
  clearTimeout(pollTimer);
  if (!jobId || rechecking) return;
  const sequence = ++pollSequence, currentJobId = jobId, query = resultQuery();
  $('results-panel').setAttribute('aria-busy','true');
  try {
    const job = await api(`/api/search/jobs/${jobId}?${query}`);
    if (sequence !== pollSequence || currentJobId !== jobId || rechecking) return;
    airportLabels = job.airports || {};
    resultJob = job; offset = job.offset;
    syncResultFilters(job);
    $('filter-error').hidden = true;
    if (resultView === null) resultView = job.plan.candidate_count > 1 ? 'destinations' : 'trips';
    if (restorePending) { restorePending = false; restoreForm(job.request, job.plan); }
    $('progress-panel').hidden = false; $('results-panel').hidden = false;
    $('status-label').textContent = states[job.status] || job.status;
    $('progress').max = job.total || 1; $('progress').value = job.completed;
    $('progress-text').textContent = `${job.completed} / ${countLabel(job.total,'combinazione esaminata','combinazioni esaminate')} · ${countLabel(job.result_count,'viaggio trovato','viaggi trovati')}`;
    $('current').textContent = (job.current || '').replace(/\b[A-Z0-9]{3}\b/g,code => airportLabels[code]?.city || code);
    $('job-summary').textContent = `${job.request.origins.map(code=>airportInfo(code).city).join(' + ')} · ${localDay(job.request.start_date)}${job.request.start_date === job.request.end_date ? '' : ' → '+localDay(job.request.end_date)} · budget ${money(job.request.budget)} ${tripLabel(job.request)}${job.request.trip_type === 'one_way' ? '' : ' · '+nightsLabel(job.request)}`;
    $('issues').textContent = job.issues.join(' '); $('issues').hidden = !job.issues.length;
    $('cancel').hidden = !['running','cancelling'].includes(job.status);
    $('cancel').disabled = job.status === 'cancelling';
    $('resume').hidden = !['cancelled','blocked','interrupted'].includes(job.status);
    $('results-heading').textContent = `${countLabel(job.filtered_count,'viaggio','viaggi')} · ${tripLabel(job.request)}`;
    $('result-note').textContent = `${job.plan.coverage}. ${job.unverified} righe senza dati sufficienti escluse. ${job.status === 'complete' ? '' : 'Risultati parziali. '}Gli orari sono locali agli aeroporti. I prezzi di oltre 5 minuti richiedono un nuovo controllo.`;
    $('calendar-panel').hidden = !(job.calendar?.length);
    $('calendar').innerHTML = (job.calendar || []).map(item => `<div><span>${localDay(item.date)}${item.return_date ? '<br>→ '+localDay(item.return_date) : ''}</span><strong>${money(item.price)}</strong></div>`).join('');
    const version = `${job.updated_at}|${query}|${resultView}|${job.results.map(x=>x.needs_recheck).join(',')}`;
    if (rendering !== version) {
      rendering = version;
      renderExploration(job);
    }
    if (focusResult) {
      const target = job.focused_id && document.querySelector(`#results [data-id="${CSS.escape(job.focused_id)}"]`);
      focusResult = '';
      if (target) {
        target.classList.add('trip-focused'); target.tabIndex = -1;
        target.scrollIntoView({behavior:'smooth',block:'start'}); target.focus({preventScroll:true});
      } else $('result-feedback').textContent = 'Questo viaggio non rientra più nei risultati. I suggerimenti sono stati aggiornati.';
    }
    if (['running','cancelling'].includes(job.status)) pollTimer = setTimeout(poll,2500);
    else if (job.status === 'complete') pollTimer = setTimeout(poll,30000);
  } catch(e) {
    if (sequence !== pollSequence || currentJobId !== jobId) return;
    if ($('results-panel').hidden) error(e.message);
    $('filter-error').textContent = e.message + ' I risultati mostrati non sono stati aggiornati.'; $('filter-error').hidden = false;
    if (e.status === 404) {
      jobId = null; restorePending = false;
      try { localStorage.removeItem('flight-finder-job'); } catch { /* storage optional */ }
      return;
    }
    if (e.status !== 400) pollTimer = setTimeout(poll, 10000);
  } finally { if (sequence === pollSequence) $('results-panel').setAttribute('aria-busy','false'); }
}
function syncOptions(id, options, allLabel) {
  const el = $(id), value = el.value;
  const markup = '<option value="">' + allLabel + '</option>' + options.map(([code,label])=>`<option value="${escapeHTML(code)}">${escapeHTML(label)}</option>`).join('');
  if (el.dataset.options !== markup) {
    el.innerHTML = markup; el.dataset.options = markup;
    if (value && !options.some(([code])=>code === value)) {
      const option = new Option(value+' · non più disponibile',value); el.add(option);
    }
    el.value = value;
  }
}
function syncResultFilters(job) {
  const f = job.facets, names = codes => codes.map(code=>[code,airportInfo(code).name]).sort((a,b)=>a[1].localeCompare(b[1],'it'));
  syncOptions('filter-origin',names(f.origins),'Tutti gli aeroporti');
  syncOptions('filter-destination',names(f.destinations),'Tutte le destinazioni');
  syncOptions('filter-airline',f.airlines.map(a=>[a.code,a.name]),'Tutte le compagnie');
  const options = $('excluded-options'), version = JSON.stringify(names(f.destinations));
  if (options.dataset.version !== version) {
    const selected = new Set([...options.querySelectorAll('input:checked')].map(el=>el.value));
    options.innerHTML = names(f.destinations).map(([code,name])=>`<label class="check"><input type="checkbox" value="${escapeHTML(code)}" ${selected.has(code) ? 'checked' : ''}>${escapeHTML(name)}</label>`).join('');
    options.dataset.version = version;
  }
  const round = job.request.trip_type !== 'one_way';
  showFields('filter-hours-field',round); showFields('filter-home-field',round);
  $('excluded-count').textContent = options.querySelectorAll('input:checked').length ? '('+options.querySelectorAll('input:checked').length+')' : '';
  $('filter-count').textContent = `${job.filtered_count} di ${countLabel(job.result_count,'viaggio trovato','viaggi trovati')} · ${countLabel(job.groups.length,'destinazione','destinazioni')}`;
}
function resetResultFilters() {
  for (const id of Object.values(filterFields)) $(id).value = '';
  $('excluded-options').querySelectorAll('input').forEach(el=>el.checked=false);
}
function refreshResults() {
  clearTimeout(filterTimer); pollSequence++; clearTimeout(pollTimer);
  if (!$('result-filters').reportValidity()) { $('results-panel').setAttribute('aria-busy','false'); return; }
  offset = 0; focusResult = ''; rendering = ''; poll();
}
$('result-filters').onsubmit = event => { event.preventDefault(); refreshResults(); };
$('result-filters').onreset = event => { event.preventDefault(); resetResultFilters(); refreshResults(); };
$('result-filters').oninput = () => {
  clearTimeout(filterTimer); pollSequence++; clearTimeout(pollTimer);
  filterTimer = setTimeout(refreshResults,250);
};
function recommendationCard(title, trip, explanation, kind) {
  if (!trip) return `<div class="recommendation unavailable"><span class="recommendation-label">${title}</span><p>${explanation}</p></div>`;
  const timeText = trip.useful_minutes != null ? ` · ${duration(trip.useful_minutes)} ${trip.destination_airport_change ? 'tra i voli' : 'a destinazione'}` : '';
  return `<button type="button" class="recommendation ${kind}" data-trip="${escapeHTML(trip.id)}"><span class="recommendation-label">${title}</span><strong class="recommendation-city">${escapeHTML(airportInfo(trip.destination).city)}</strong><span class="recommendation-price">${money(trip.price)}</span><span class="recommendation-dates">Da ${escapeHTML(airportInfo(trip.origin).city)} · ${shortDay(trip.date)}${trip.return_date ? ' → '+shortDay(trip.return_date) : ''}${timeText}</span><span class="recommendation-reason">${explanation}</span><span class="recommendation-footer">${trip.needs_recheck ? 'Prezzo da aggiornare' : 'Prezzo osservato su Google'}<b>Vedi viaggio ↗</b></span></button>`;
}
function renderRecommendations(job) {
  const r = job.recommendations, round = job.request.trip_type !== 'one_way';
  $('recommendations-panel').hidden = !r.cheapest;
  $('recommendations-title').textContent = round ? 'Tre modi di scegliere' : 'La partenza più economica';
  $('recommendation-scope').textContent = job.status === 'complete' ? 'Tra i risultati filtrati' : 'Tra i risultati filtrati · ricerca parziale';
  $('recommendation-method').hidden = !round;
  const basis = r.cheapest ? `${escapeHTML(airportInfo(r.cheapest.destination).city)}, ${shortDay(r.cheapest.date)}, ${money(r.cheapest.price)}` : '';
  $('recommendations').innerHTML = recommendationCard('Il più economico',r.cheapest,'Il prezzo totale più basso tra i viaggi che rispettano i tuoi filtri.','cheapest') +
    (round ? recommendationCard('Più tempo a destinazione',r.longest,r.longest?.id === r.cheapest?.id ? 'È anche il più economico: nessun altro viaggio confrontabile offre più tempo.' : r.longest ? 'Il soggiorno più lungo tra i risultati filtrati, senza cambio di aeroporto a destinazione.' : 'Nessun viaggio con tempo a destinazione confrontabile.','longest') +
    recommendationCard('Il miglior compromesso',r.balanced,r.balanced ? `Con ${money(r.balanced.extra_price)} in più guadagni ${duration(r.balanced.extra_minutes)} rispetto a ${basis}.` : r.cheapest?.destination_airport_change ? 'Il più economico riparte da un altro aeroporto: il tempo del trasferimento non è noto, quindi non stimiamo un compromesso.' : 'Non emerge un’alternativa con più tempo a un piccolo sovrapprezzo. Puoi già scegliere il più economico.','balanced') : '');
}
function renderExploration(job) {
  renderRecommendations(job);
  const grouped = resultView === 'destinations';
  $('view-destinations').setAttribute('aria-pressed',String(grouped));
  $('view-trips').setAttribute('aria-pressed',String(!grouped));
  $('back-destinations').hidden = !$('filter-destination').value;
  $('destination-groups').hidden = !grouped;
  $('results').hidden = grouped;
  $('result-pagination').hidden = grouped || !job.filtered_count;
  const empty = job.result_count
    ? '<div class="panel empty-results"><h3>Nessun viaggio con questi filtri</h3><p>I viaggi trovati sono ancora disponibili. Riduci il tempo minimo, alza il prezzo massimo o azzera i filtri qui sopra.</p><button type="button" data-reset-results>Azzera filtri sui risultati</button></div>'
    : '<div class="panel empty-results"><h3>Nessun viaggio confermato finora</h3><p class="hint">Una ricerca incompleta o bloccata non dimostra che non esistano voli. Le rotte senza un prezzo osservato restano fuori dai risultati.</p></div>';
  $('destination-groups').innerHTML = job.groups.length ? job.groups.map(group => {
    const t = group.featured, airport = airportInfo(group.destination);
    const choice = {price:'La più economica',useful:'Più tempo disponibile',duration:'Voli più brevi',departure:'Prima partenza'}[$('result-sort').value];
    return `<article class="destination-card"><div class="destination-top"><div><p class="eyebrow">${countLabel(group.count,'combinazione','combinazioni')}</p><h3>${escapeHTML(airport.city)}</h3><p class="hint">${escapeHTML(airport.name)}</p></div><div class="destination-price"><span>${tripLabel(job.request)} da</span><strong>${money(group.cheapest.price)}</strong></div></div><div class="destination-best"><span>${choice} · ${money(t.price)} · da ${escapeHTML(airportInfo(t.origin).city)}</span><b>${shortDay(t.date)}${t.return_date ? ' → '+shortDay(t.return_date) : ''}</b>${t.useful_minutes != null ? '<span>'+duration(t.useful_minutes)+(t.destination_airport_change ? ' tra i voli' : ' a destinazione')+'</span>' : ''}</div><div class="destination-footer"><small>${group.cheapest.needs_recheck || t.needs_recheck ? 'Prezzi da aggiornare' : 'Prezzi osservati su Google'}</small><button type="button" data-destination="${escapeHTML(group.destination)}">Vedi ${countLabel(group.count,'viaggio','viaggi')} →</button></div></article>`;
  }).join('') : empty;
  $('results').innerHTML = job.results.length ? job.results.map(card).join('') : empty;
  $('prev-page').disabled = offset === 0; $('next-page').disabled = !job.has_more;
  $('page-label').textContent = job.filtered_count ? `${offset+1}–${offset+job.results.length} di ${job.filtered_count}` : '0 risultati';
}
for (const view of ['destinations','trips']) $('view-'+view).onclick = () => {
  resultView = view; if (resultJob) renderExploration(resultJob);
};
$('back-destinations').onclick = () => { $('filter-destination').value = ''; resultView = 'destinations'; refreshResults(); };
$('destination-groups').onclick = event => {
  const button = event.target.closest('[data-destination]');
  if (!button) return;
  $('filter-destination').value = button.dataset.destination; resultView = 'trips'; refreshResults();
};
$('recommendations').onclick = event => {
  const button = event.target.closest('[data-trip]'); if (!button) return;
  clearTimeout(filterTimer); focusResult = button.dataset.trip; resultView = 'trips'; rendering = ''; poll();
};
$('results-panel').addEventListener('click',event=>{
  if (event.target.closest('[data-reset-results]')) { resetResultFilters(); refreshResults(); }
});
const planeIcon = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="m21 12-8-5V3.5a1.5 1.5 0 0 0-3 0V7l-8 5v2l8-2.5V18l-2 1.5V21l3.5-1 3.5 1v-1.5L13 18v-6.5l8 2.5z"/></svg>';
function airlineBadge(segment) {
  const code = segment.airline_code, name = segment.airline_name || code;
  const image = /^[A-Z0-9]{2}$/.test(code) ? `<img class="airline-logo" src="https://www.gstatic.com/flights/airline_logos/70px/${encodeURIComponent(code)}.png" alt="" width="34" height="34" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : '';
  return `<span class="airline-mark"><span class="airline-initial" aria-hidden="true">${escapeHTML(name.slice(0,1))}</span>${image}</span><span class="carrier-label"><span class="carrier-name">${escapeHTML(name)}</span><span class="flight-number">Volo ${escapeHTML(code)} ${escapeHTML(segment.flight_number)}</span></span>`;
}
function airportEndpoint(code, instant, arrival=false, departureDate=null) {
  const info = airportInfo(code);
  const changedDate = departureDate && instant.slice(0,10) !== departureDate.slice(0,10);
  return `<div class="flight-endpoint ${arrival ? 'flight-arrival' : ''}"><time datetime="${escapeHTML(instant)}" class="flight-time">${localTime(instant)}</time><span class="airport-city">${escapeHTML(info.city)}</span><span class="airport-name" title="${escapeHTML(info.name)}">${escapeHTML(info.name)}</span>${changedDate ? `<span class="arrival-day">Arrivo ${shortDay(instant)}</span>` : ''}</div>`;
}
function leg(quote, title) {
  const first = quote.segments[0], last = quote.segments.at(-1), stops = quote.segments.length-1;
  const stopText = stops ? countLabel(stops,'scalo','scali') : 'Diretto';
  const carriers = quote.segments.map(segment => `<span class="carrier">${airlineBadge(segment)}</span>`).join('');
  return `<section class="flight-leg" aria-label="${title}"><div class="flight-leg-heading"><span class="direction-tag">${title === 'Andata' ? '↗' : '↙'} ${title}</span><span>${shortDay(first.departure)}</span><span class="leg-fare">${money(quote.price)}</span></div>
    <div class="flight-leg-body"><div class="carriers">${carriers}</div><div class="flight-route">
      ${airportEndpoint(first.from_code,first.departure)}
      <div class="flight-connection"><span class="flight-duration">${duration(quote.duration_min)}</span><span class="flight-track">${planeIcon}</span><span class="stop-label ${stops ? '' : 'nonstop'}">${stopText}</span></div>
      ${airportEndpoint(last.to_code,last.arrival,true,first.departure)}
    </div></div>
    ${stops ? `<details class="connection-details"><summary>Vedi le coincidenze · ${quote.segments.slice(0,-1).map(seg=>escapeHTML(airportInfo(seg.to_code).city)).join(', ')}</summary><p class="hint">${quote.self_transfer === true ? 'Coincidenza autogestita (self-transfer)' : quote.self_transfer === false ? 'Scalo protetto indicato da Google' : 'Protezione dello scalo non verificata'}</p>${quote.segments.map(seg=>`<p class="connection-segment"><b>${escapeHTML(airportInfo(seg.from_code).name)}</b> ${localTime(seg.departure)} → <b>${escapeHTML(airportInfo(seg.to_code).name)}</b> ${localTime(seg.arrival)}<span>${shortDay(seg.departure)} → ${shortDay(seg.arrival)} · ${escapeHTML(seg.airline_name)} ${escapeHTML(seg.airline_code)} ${escapeHTML(seg.flight_number)}</span></p>`).join('')}</details>` : ''}</section>`;
}
function card(pair) {
  const outbound = pair.outbound, first = outbound.segments[0], last = outbound.segments.at(-1);
  const origin = first.from_code, dest = last.to_code, round = !!pair.inbound;
  const home = round ? pair.inbound.segments.at(-1).to_code : null;
  const away = round ? pair.inbound.segments[0].from_code : null;
  const heading = `${escapeHTML(airportInfo(origin).city)} <span class="route-arrow" aria-label="verso">→</span> ${escapeHTML(airportInfo(dest).city)}`;
  const checked = new Date(pair.checked_at).toLocaleString('it-IT',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
  return `<article class="trip" data-id="${escapeHTML(pair.id)}"><div class="trip-top"><div class="trip-title"><p class="trip-dates">${shortDay(first.departure)}${round ? ' — '+shortDay(pair.inbound.segments[0].departure) : ''} · ${first.departure.slice(0,4)}</p><h3>${heading}</h3><p class="trip-subtitle">${round ? countLabel(pair.nights,'notte','notti')+' · ' : 'Sola andata · '}${countLabel(outbound.travelers,'viaggiatore','viaggiatori')}${round && home !== origin ? ' · rientro a '+escapeHTML(airportInfo(home).name) : ''}</p></div><div class="trip-price"><span class="price-caption">${round ? 'Totale andata e ritorno' : 'Totale sola andata'}</span><strong>${money(pair.price)}</strong><span>per ${pair.outbound.travelers === 1 ? '1 persona' : 'tutti i viaggiatori'}</span></div></div>
    <div class="trip-itinerary">${leg(outbound,'Andata')}${round ? leg(pair.inbound,'Ritorno') : ''}</div>
    ${round && dest !== away ? `<p class="notice">Arrivi a ${escapeHTML(airportInfo(dest).name)} e riparti da ${escapeHTML(airportInfo(away).name)}. Trasferimento durante il soggiorno da organizzare separatamente, non incluso nel prezzo.</p>` : ''}
    <div class="trip-benefits">${round ? `<span class="stay-highlight"><span aria-hidden="true">◷</span><strong>${duration(pair.useful_minutes)}</strong> ${dest === away ? 'a destinazione' : 'tra i due voli'}</span>` : ''}<span class="baggage-note">${({base:'Tariffa base',cabin:'Filtro trolley',checked:'Filtro stiva'})[outbound.baggage] || 'Bagaglio'}</span>${round ? '<span class="ticket-note">Due biglietti separati</span>' : ''}</div>
    <div class="trip-footer"><div class="quote-status ${pair.needs_recheck ? 'quote-stale' : 'quote-recent'}"><span class="status-dot" aria-hidden="true"></span><span><b>${pair.needs_recheck ? 'Prezzo da aggiornare' : 'Prezzo verificato su Google'}</b><small>Ultimo controllo ${checked}</small></span></div><div class="trip-actions"><button type="button" class="recheck">↻ Ricontrolla prezzo</button><a class="booking-link" href="${escapeHTML(safeLink(outbound.booking_url))}" target="_blank" rel="noopener noreferrer">${round ? 'Andata' : 'Apri volo'} ↗</a>${round ? `<a class="booking-link" href="${escapeHTML(safeLink(pair.inbound.booking_url))}" target="_blank" rel="noopener noreferrer">Ritorno ↗</a>` : ''}</div></div><div class="recheck-output" role="status"></div>
  </article>`;
}
$('results').addEventListener('error',event => {
  if (event.target.matches('img.airline-logo')) event.target.hidden = true;
},true);
$('results').onclick = async event => {
  const button = event.target.closest('button.recheck'); if (!button) return;
  const article = button.closest('[data-id]'), output = article.querySelector('.recheck-output');
  if (rechecking) return;
  rechecking = true; clearTimeout(pollTimer);
  button.disabled = true; output.textContent = 'Ricontrollo i voli e il prezzo…';
  $('result-feedback').textContent = '';
  try {
    const data = await api(`/api/search/jobs/${jobId}/results/${article.dataset.id}/recheck`,'POST',{});
    if (data.status === 'confirmed') {
      $('result-feedback').textContent = data.price_changed ? `Prezzo aggiornato: ${money(data.result.price)} ${data.result.inbound ? 'A/R' : 'sola andata'}.` : 'Stessi voli e prezzo confermati adesso su Google.';
    } else {
      $('result-feedback').textContent = data.message;
    }
  } catch(e) { $('result-feedback').textContent = `Non verificato: ${e.message}`; }
  finally {
    button.disabled = false; output.textContent = ''; rechecking = false; rendering = '';
    await poll();
  }
};
$('cancel').onclick = async () => { try { await api(`/api/search/jobs/${jobId}/cancel`,'POST',{}); await poll(); } catch(e){error(e.message);} };
$('resume').onclick = async () => {
  $('resume').disabled = true; error('');
  try { await api(`/api/search/jobs/${jobId}/resume`,'POST',{}); }
  catch(e) { if (e.status !== 409) error(e.message); }
  finally { $('resume').disabled = false; await poll(); }
};
$('result-sort').onchange = refreshResults;
$('prev-page').onclick = () => {offset=Math.max(0,offset-50); poll();};
$('next-page').onclick = () => {offset+=50; poll();};
try { jobId = localStorage.getItem('flight-finder-job'); } catch { /* storage optional */ }
if (jobId) poll();
