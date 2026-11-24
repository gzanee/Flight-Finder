'use strict';
const $ = id => document.getElementById(id);
const days = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'];
const NO_BUDGET = 100000;
let jobId = null, pollTimer = null, offset = 0, rendering = '';
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
const airportInfo = code => airportLabels[code] || {city:code,name:'Aeroporto '+code};
const flag = code => {
  const country = airportInfo(code).country;
  return /^[a-z]{2}$/.test(country || '') ? `<img class="flag" src="/static/flags/${country}.png" alt="" width="20" height="15">` : '';
};
// One option per city; the value lists its airports (e.g. "LGW,LTN,STN").
function cityOptions(codes) {
  const cities = new Map();
  for (const code of codes) {
    const city = airportInfo(code).city;
    cities.set(city, [...(cities.get(city) || []), code]);
  }
  return [...cities].map(([city,list]) => [list.sort().join(','), `${city} (${list.join(', ')})`]).sort((a,b)=>a[1].localeCompare(b[1],'it'));
}
const shortDay = iso => new Date(iso.slice(0,10)+'T12:00:00').toLocaleDateString('it-IT',{weekday:'short',day:'numeric',month:'short'});
const money = n => new Intl.NumberFormat('it-IT', {style:'currency',currency:'EUR',maximumFractionDigits:n % 1 ? 2 : 0}).format(n);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const duration = minutes => `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;
const countLabel = (n, single, plural) => `${n} ${n === 1 ? single : plural}`;
const tripLabel = spec => spec.trip_type === 'one_way' ? 'solo andata' : 'andata e ritorno';
const localTime = iso => iso.slice(11,16);
const localDay = iso => new Date(iso.slice(0,10) + 'T12:00:00').toLocaleDateString('it-IT', {weekday:'short',day:'numeric',month:'short'});

const nightsBetween = (a, b) => Math.round((new Date(b.slice(0,10)+'T12:00:00') - new Date(a.slice(0,10)+'T12:00:00')) / 864e5);
function renderCalendar(items) {
  if (!items.length) return '';
  const byDay = new Map();
  for (const item of items) {
    const key = item.date.slice(0,10);
    if (!byDay.has(key)) byDay.set(key, []);
    byDay.get(key).push(item);
  }
  const best = [...byDay.values()].map(list => Math.min(...list.map(x => x.price)));
  const lo = Math.min(...best), hi = Math.max(...best);
  const tier = p => hi === lo || p <= lo + (hi-lo)/3 ? 'cheap' : p <= lo + 2*(hi-lo)/3 ? 'mid' : 'high';
  const keys = [...byDay.keys()].sort();
  const months = [];
  for (let d = new Date(keys[0]+'T12:00:00'); d <= new Date(keys.at(-1)+'T12:00:00'); d.setMonth(d.getMonth()+1, 1)) months.push([d.getFullYear(), d.getMonth()]);
  const pad = n => String(n).padStart(2,'0');
  const weekdays = ['lun','mar','mer','gio','ven','sab','dom'].map(w => `<span class="cal-wd">${w}</span>`).join('');
  const html = months.map(([y, m]) => {
    const first = new Date(y, m, 1, 12), days = new Date(y, m+1, 0).getDate();
    const cells = Array.from({length:(first.getDay()+6)%7}, () => '<span class="cal-empty"></span>');
    for (let day = 1; day <= days; day++) {
      const key = `${y}-${pad(m+1)}-${pad(day)}`, list = byDay.get(key);
      if (!list) { cells.push(`<span class="cal-day cal-none"><b>${day}</b></span>`); continue; }
      list.sort((a,b) => a.price - b.price);
      const top = list[0], isBest = top.price === lo;
      const nights = top.return_date ? nightsBetween(top.date, top.return_date) : null;
      const sub = list.length > 1 ? `${list.length} ritorni` : nights === null ? 'solo andata' : nights === 0 ? 'in giornata' : `${nights} ${nights === 1 ? 'notte' : 'notti'}`;
      const tip = list.map(x => `${localDay(x.date)}${x.return_date ? ' → '+localDay(x.return_date) : ''}: ${money(x.price)}`).join('\n');
      cells.push(`<span class="cal-day cal-${tier(top.price)}${isBest ? ' cal-best' : ''}" title="${escapeHTML(tip)}"><b>${day}</b><strong>${money(top.price)}</strong><small>${sub}</small>${isBest ? '<i>top</i>' : ''}</span>`);
    }
    const title = first.toLocaleDateString('it-IT', {month:'long', year:'numeric'});
    return `<section class="cal-month"><h4>${title}</h4><div class="cal-grid">${weekdays}${cells.join('')}</div></section>`;
  }).join('');
  const legend = `<div class="cal-legend"><span><i class="cal-cheap"></i>più economico</span><span><i class="cal-mid"></i>nella media</span><span><i class="cal-high"></i>più caro</span><span class="cal-hint">Prezzo minimo per giorno di partenza · passa sopra un giorno per vedere i ritorni</span></div>`;
  return legend + `<div class="cal-months">${html}</div>`;
}
const dateISO = value => `${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,'0')}-${String(value.getDate()).padStart(2,'0')}`;
const addDays = (iso, n) => { const d = new Date(iso+'T12:00:00'); d.setDate(d.getDate()+n); return dateISO(d); };
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
function safeLink(url) {
  try { const u = new URL(url); return u.protocol === 'https:' && u.hostname === 'www.google.com' ? u.href : '#'; }
  catch { return '#'; }
}
async function api(path, method='GET', body) {
  const response = await fetch(path, {method, headers:{'Content-Type':'application/json'},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) { const failure = new Error(data.error || 'Qualcosa è andato storto. Riprova.'); failure.status = response.status; throw failure; }
  return data;
}

// Airports
const selectedAirports = {
  origins: new Map([['VCE','Venezia'],['TSF','Treviso']]), destinations: new Map()
};
// A whole country is one entry, keyed "@" + ISO code, holding its airports.
function renderAirports(kind) {
  $(kind+'-chips').innerHTML = [...selectedAirports[kind]].map(([code,name]) => name.codes
    ? `<button type="button" class="airport-chip" data-code="${escapeHTML(code)}" aria-label="Rimuovi ${escapeHTML(name.name)}" title="${escapeHTML(name.cities.join(', '))}"><img class="flag" src="/static/flags/${escapeHTML(name.country)}.png" alt="" width="20" height="15">${escapeHTML(name.name)} <b>${name.codes.length} aeroporti</b><span aria-hidden="true">×</span></button>`
    : `<button type="button" class="airport-chip" data-code="${escapeHTML(code)}" aria-label="Rimuovi ${escapeHTML(name)}">${escapeHTML(name)}${name === code ? '' : ` <b>${escapeHTML(code)}</b>`}<span aria-hidden="true">×</span></button>`).join('');
}
const destinationCodes = () => [...new Set([...selectedAirports.destinations].flatMap(([code,name]) => name.codes || [code]))];
function addCountry(hit) {
  const codes = hit.airports.map(a => a.code);
  if (new Set([...destinationCodes(), ...codes]).size > 200) return error('Hai raggiunto il numero massimo di aeroporti.');
  const map = selectedAirports.destinations;
  codes.forEach(code => map.delete(code));  // the country already includes them
  map.set('@'+hit.country, {name:hit.city, country:hit.country, codes, cities:hit.airports.map(a => a.city)});
  renderAirports('destinations');
}
function addAirport(kind, airport) {
  const map = selectedAirports[kind];
  if (!map.has(airport.code) && map.size >= (kind === 'origins' ? 10 : 200)) return error('Hai raggiunto il numero massimo di aeroporti.');
  map.set(airport.code, airport.city || airport.name || airport.code);
  renderAirports(kind);
}
// Whole countries as a destination: the airports linked to the chosen origins,
// named in Italian by the browser. Loaded once per set of origins.
const fold = text => text.normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
const countryNames = new Intl.DisplayNames(['it'],{type:'region'});
let countryCatalog = {key:null, list:[]};
async function countryHits(q) {
  const key = [...selectedAirports.origins.keys()].join(',');
  if (countryCatalog.key !== key) {
    const data = await api('/api/countries?origins='+encodeURIComponent(key));
    countryCatalog = {key, list:Object.entries(data.countries).map(([country,entry]) => ({country, city:countryNames.of(country.toUpperCase()), ...entry}))};
  }
  const needle = fold(q);
  return countryCatalog.list.filter(c => fold(c.city).startsWith(needle) || c.country === needle)
    .sort((a,b) => a.city.localeCompare(b.city,'it')).slice(0,3);
}
for (const kind of ['origins','destinations']) {
  const input = $(kind+'-query'), suggestions = $(kind+'-suggestions');
  let timer, sequence = 0, hits = [], active = -1;
  const close = () => { sequence++; suggestions.hidden = true; input.setAttribute('aria-expanded','false'); input.removeAttribute('aria-activedescendant'); active = -1; };
  const choose = hit => { hit.airports ? addCountry(hit) : addAirport(kind,hit); input.value = ''; close(); input.focus(); };
  $(kind+'-chips').onclick = event => {
    const button = event.target.closest('[data-code]'); if (!button) return;
    selectedAirports[kind].delete(button.dataset.code); renderAirports(kind);
  };
  suggestions.onclick = event => { const button = event.target.closest('[data-index]'); if (button) choose(hits[Number(button.dataset.index)]); };
  input.oninput = () => {
    clearTimeout(timer); close();
    const q = input.value.trim(), current = sequence;
    if (q.length < 2) return;
    timer = setTimeout(async () => {
      try {
        const data = await api('/api/airports?q='+encodeURIComponent(q));
        if (sequence !== current || input.value.trim() !== q) return;
        const countries = kind === 'destinations' ? await countryHits(q).catch(() => []) : [];
        if (sequence !== current || input.value.trim() !== q) return;
        const chosen = kind === 'destinations' ? new Set(destinationCodes()) : selectedAirports[kind];
        hits = [...countries.filter(c => !selectedAirports[kind].has('@'+c.country)), ...data.airports.filter(a => !chosen.has(a.code))];
        suggestions.innerHTML = hits.map((a,i) => `<button type="button" role="option" aria-selected="false" id="${kind}-option-${i}" data-index="${i}"><span>${escapeHTML(a.city || a.name)}<small>${a.airports ? (a.linked ? `Tutto il paese · ${countLabel(a.airports.length,'aeroporto collegato','aeroporti collegati')}` : `Senza voli diretti · ${escapeHTML([...new Set(a.airports.map(x => x.city))].join(', '))}`) : escapeHTML(a.name)}</small></span>${a.airports ? `<img class="flag" src="/static/flags/${escapeHTML(a.country)}.png" alt="" width="20" height="15">` : `<b>${escapeHTML(a.code)}</b>`}</button>`).join('');
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
    if (event.key === 'Backspace' && !input.value && selectedAirports[kind].size) {
      selectedAirports[kind].delete([...selectedAirports[kind].keys()].at(-1)); renderAirports(kind); return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      if (!suggestions.hidden) return choose(hits[Math.max(active,0)]);
      const codes = input.value.toUpperCase().trim().split(/[\s,;]+/);
      if (codes.every(code => /^[A-Z0-9]{3}$/.test(code))) { codes.forEach(code => addAirport(kind,{code})); input.value = ''; close(); }
      else error('Scegli un aeroporto tra quelli suggeriti.');
    }
  };
  document.addEventListener('click', event => { if (!$(kind+'-picker').contains(event.target)) close(); });
  $(kind+'-picker').addEventListener('click', event => { if (!event.target.closest('button')) input.focus(); });
  renderAirports(kind);
}

// Trip type and date mode as segmented controls over the hidden selects.
function syncSegmented() {
  document.querySelectorAll('.segmented').forEach(group => {
    const value = $(group.dataset.target).value;
    group.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed',String(b.dataset.value === value)));
  });
}
document.querySelectorAll('.segmented').forEach(group => group.onclick = event => {
  const button = event.target.closest('button'); if (!button) return;
  $(group.dataset.target).value = button.dataset.value;
  if (group.dataset.target === 'date-mode' && button.dataset.value === 'fixed' && $('end-date').value > addDays($('start-date').value,14)) $('end-date').value = addDays($('start-date').value,3);
  updateModes();
});

// Travelers popover
function travelersSummary() {
  const n = id => Number($(id).value) || 0;
  const people = [countLabel(n('adults'),'adulto','adulti')];
  if (n('children')) people.push(countLabel(n('children'),'bambino','bambini'));
  if (n('infants')) people.push(countLabel(n('infants'),'neonato','neonati'));
  $('travelers-summary').textContent = `${people.join(', ')} · ${$('seat').selectedOptions[0].textContent}`;
}
$('travelers').addEventListener('click', event => {
  const button = event.target.closest('[data-step]'); if (!button) return;
  const input = $(button.dataset.step);
  input.value = Math.min(Number(input.max), Math.max(Number(input.min), (Number(input.value) || 0) + Number(button.dataset.delta)));
  travelersSummary();
});
$('travelers').addEventListener('input', travelersSummary);
document.addEventListener('click', event => { if (!$('travelers').contains(event.target)) $('travelers').open = false; });

for (const [id, selected] of [['departure-days',4],['return-days',6]]) {
  $(id).innerHTML = days.map((d,i) => `<label><input type="checkbox" value="${i}" ${i === selected ? 'checked' : ''}><span>${d}</span></label>`).join('');
}
const today = dateISO(new Date());
$('start-date').value = addDays(today,1); $('start-date').min = today;
$('end-date').value = addDays(today,31); $('end-date').min = today;
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
  $('start-label').textContent = flexible ? 'Dal' : 'Partenza';
  $('end-label').textContent = flexible ? 'Al' : 'Ritorno';
  $('period-hint').textContent = flexible ? (round ? 'Confrontiamo tutte le partenze e i ritorni possibili in questo periodo, nei giorni e con le notti che scegli.' : 'Confrontiamo tutte le partenze possibili in questo periodo, nei giorni che scegli.') : '';
  $('period-hint').hidden = !flexible;
  $('budget-label').textContent = round ? 'Budget A/R' : 'Budget';
  $('deadline-label').textContent = round ? 'A casa entro le (giorno del ritorno)' : 'Arrivo entro le (giorno della partenza)';
  syncSegmented();
}
$('anywhere').onchange = updateModes;
$('preset-weekend').onclick = () => {
  $('trip-type').value = 'round_trip'; $('date-mode').value = 'flexible';
  for (const [id,day] of [['departure-days',4],['return-days',6]]) $(id).querySelectorAll('input').forEach(el => el.checked = Number(el.value) === day);
  $('min-nights').value = '2'; $('max-nights').value = '2';
  for (const prefix of ['departure','return']) { $(prefix+'-min').value = '17:00'; $(prefix+'-max').value = '23:59'; }
  updateModes();
};
$('preset-anytime').onclick = () => {
  document.querySelectorAll('.days input').forEach(el => el.checked = true);
  for (const prefix of ['departure','return']) { $(prefix+'-min').value = '00:00'; $(prefix+'-max').value = '23:59'; }
};
function restoreForm(spec) {
  for (const kind of ['origins','destinations']) {
    selectedAirports[kind] = new Map(spec[kind].map(code => [code, airportLabels[code]?.city || code])); renderAirports(kind);
  }
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
  if (spec.budget === NO_BUDGET) $('budget').value = '';
  updateModes(); travelersSummary();
}
function values() {
  const num = (id,fallback=0) => $(id).value === '' ? fallback : Number($(id).value);
  for (const kind of ['origins','destinations']) {
    if (kind === 'destinations' && $('anywhere').checked) continue;
    const text = $(kind+'-query').value.trim();
    if (text) {
      const codes = text.toUpperCase().split(/[\s,;]+/);
      if (!codes.every(code => /^[A-Z0-9]{3}$/.test(code))) throw new Error('Scegli gli aeroporti tra quelli suggeriti prima di cercare.');
      codes.forEach(code => addAirport(kind,{code})); $(kind+'-query').value = '';
    }
  }
  if (!selectedAirports.origins.size) throw new Error('Da dove parti? Aggiungi almeno un aeroporto.');
  const round = $('trip-type').value === 'round_trip', flexible = $('date-mode').value === 'flexible';
  const spec = {
    origins:[...selectedAirports.origins.keys()],destinations:$('anywhere').checked ? [] : destinationCodes(),
    trip_type:$('trip-type').value, date_mode:$('date-mode').value,destination_set:$('destination-set').value,
    start_date:$('start-date').value, end_date:round || flexible ? $('end-date').value : $('start-date').value,
    departure_days:[...$('departure-days').querySelectorAll('input:checked')].map(el => Number(el.value)),
    return_days:[...$('return-days').querySelectorAll('input:checked')].map(el => Number(el.value)),
    min_nights:round && flexible ? num('min-nights') : 2, max_nights:round && flexible ? num('max-nights') : 2,
    budget:num('budget',NO_BUDGET), adults:num('adults'),children:num('children'),infants:num('infants'),max_stops:num('max-stops'),
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
  if (!$('anywhere').checked && !spec.destinations.length) throw new Error('Dove vuoi andare? Aggiungi una destinazione oppure scegli Ovunque.');
  return spec;
}

// One click: validate and start.
$('trip-form').onsubmit = async event => {
  event.preventDefault(); error('');
  if (rechecking) return error('Attendi la fine dell’aggiornamento del prezzo.');
  $('plan-button').disabled = true; $('plan-button').classList.add('loading');
  try {
    const body = values();
    await api('/api/search/plan','POST',body);
    const data = await api('/api/search/jobs','POST',body);
    jobId = data.id; offset = 0; rendering = ''; restorePending = false;
    resetResultFilters(); resultView = null; focusResult = '';
    $('result-feedback').textContent = '';
    try { localStorage.setItem('flight-finder-job',jobId); } catch { /* storage optional */ }
    history.replaceState(null,'',`?ricerca=${jobId}`);
    await poll(); $('progress-panel').scrollIntoView({behavior:'smooth',block:'start'});
  } catch(e) { error(e.message); }
  finally { $('plan-button').disabled = false; $('plan-button').classList.remove('loading'); }
};
updateModes(); travelersSummary();

const states = {running:'Stiamo cercando i voli migliori…',cancelling:'Ci fermiamo…',cancelled:'Ricerca fermata',blocked:'Ricerca in pausa',interrupted:'Ricerca interrotta',complete:'Ricerca completata'};
// The server measures the pace of this search; before its first requests it
// has no estimate yet (eta_seconds is null).
function remaining(job) {
  if (job.eta_seconds == null) return 'qualche minuto';
  const minutes = Math.ceil(job.eta_seconds / 60);
  return minutes <= 1 ? 'meno di un minuto' : `circa ${minutes} minuti`;
}
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
    syncExport(job);
    $('filter-error').hidden = true;
    if (resultView === null) resultView = job.plan.candidate_count > 1 ? 'destinations' : 'trips';
    if (restorePending) { restorePending = false; restoreForm(job.request); }
    const running = ['running','cancelling'].includes(job.status);
    $('features').hidden = true; document.body.classList.add('has-results');
    $('progress-panel').hidden = job.status === 'complete'; $('results-panel').hidden = false;
    $('progress-panel').dataset.status = job.status;
    $('status-label').textContent = states[job.status] || job.status;
    $('progress').max = job.total || 1; $('progress').value = job.completed;
    $('progress-text').textContent = running
      ? `${countLabel(job.result_count,'viaggio trovato','viaggi trovati')} finora · ancora ${remaining(job)}`
      : `${countLabel(job.result_count,'viaggio trovato','viaggi trovati')}`;
    $('current').textContent = running && job.current ? 'Ora: ' + job.current.replace(/\b[A-Z0-9]{3}\b/g,code => airportLabels[code]?.city || code) : '';
    const r = job.request;
    $('job-summary').textContent = `${r.origins.map(code=>airportInfo(code).city).join(' + ')} → ${r.destinations.length ? r.destinations.map(code=>airportInfo(code).city).join(', ') : 'ovunque'} · ${localDay(r.start_date)}${r.start_date === r.end_date ? '' : ' – '+localDay(r.end_date)} · ${countLabel(r.adults+r.children+r.infants,'viaggiatore','viaggiatori')}`;
    $('issues').textContent = job.issues.join(' '); $('issues').hidden = !job.issues.length;
    $('cancel').hidden = !running;
    $('cancel').disabled = job.status === 'cancelling';
    $('resume').hidden = !['cancelled','blocked','interrupted'].includes(job.status);
    $('results-heading').textContent = job.filtered_count ? `${countLabel(job.filtered_count,'viaggio','viaggi')} ${r.trip_type === 'one_way' ? 'di sola andata' : 'andata e ritorno'}` : (running ? 'Cerchiamo…' : 'Nessun viaggio');
    $('result-note').textContent = `${job.status === 'complete' ? '' : 'I risultati si aggiornano mentre cerchiamo. '}Prezzi totali per ${countLabel(r.adults+r.children+r.infants,'viaggiatore','viaggiatori')}, orari locali.`;
    $('calendar-panel').hidden = !(job.calendar?.length > 1);
    $('calendar').innerHTML = renderCalendar(job.calendar || []);
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
      } else $('result-feedback').textContent = 'Questo viaggio non è più tra i risultati.';
    }
    if (running) pollTimer = setTimeout(poll,2500);
    else if (job.status === 'complete') pollTimer = setTimeout(poll,30000);
  } catch(e) {
    if (sequence !== pollSequence || currentJobId !== jobId) return;
    if ($('results-panel').hidden) error(e.message);
    $('filter-error').textContent = e.message; $('filter-error').hidden = false;
    if (e.status === 404) {
      jobId = null; restorePending = false;
      $('results-panel').hidden = true; $('progress-panel').hidden = true; $('features').hidden = false; error('');
      document.body.classList.remove('has-results');
      try { localStorage.removeItem('flight-finder-job'); } catch { /* storage optional */ }
      history.replaceState(null,'',location.pathname);
      return;
    }
    if (e.status !== 400) pollTimer = setTimeout(poll, 10000);
  } finally { if (sequence === pollSequence) $('results-panel').setAttribute('aria-busy','false'); }
}

// Result filters
function syncOptions(id, options, allLabel) {
  const el = $(id), value = el.value;
  const markup = '<option value="">' + allLabel + '</option>' + options.map(([code,label])=>`<option value="${escapeHTML(code)}">${escapeHTML(label)}</option>`).join('');
  if (el.dataset.options !== markup) {
    el.innerHTML = markup; el.dataset.options = markup;
    if (value && !options.some(([code])=>code === value)) el.add(new Option(value,value));
    el.value = value;
  }
}
function syncResultFilters(job) {
  const f = job.facets, names = codes => codes.map(code=>[code,`${airportInfo(code).city} (${code})`]).sort((a,b)=>a[1].localeCompare(b[1],'it'));
  syncOptions('filter-origin',names(f.origins),'Tutti gli aeroporti');
  syncOptions('filter-destination',cityOptions(f.destinations),'Tutte');
  syncOptions('filter-airline',f.airlines.map(a=>[a.code,a.name]),'Tutte');
  const options = $('excluded-options'), version = JSON.stringify(cityOptions(f.destinations));
  if (options.dataset.version !== version) {
    const selected = new Set([...options.querySelectorAll('input:checked')].map(el=>el.value));
    options.innerHTML = cityOptions(f.destinations).map(([code,name])=>`<label class="check"><input type="checkbox" value="${escapeHTML(code)}" ${selected.has(code) ? 'checked' : ''}>${escapeHTML(name)}</label>`).join('');
    options.dataset.version = version;
  }
  const round = job.request.trip_type !== 'one_way';
  showFields('filter-hours-field',round); showFields('filter-home-field',round);
  const excluded = options.querySelectorAll('input:checked').length;
  $('excluded-count').textContent = excluded ? '('+excluded+')' : '';
  $('filter-count').textContent = job.filtered_count === job.result_count ? '' : `${job.filtered_count} di ${job.result_count}`;
}
// The PDF follows the active filters; sorting and paging do not apply to it.
function syncExport(job) {
  const params = new URLSearchParams(resultQuery());
  for (const key of ['sort','offset','focus']) params.delete(key);
  const query = params.toString();
  $('export-pdf').href = `/api/search/jobs/${jobId}/export.pdf${query ? '?'+query : ''}`;
  $('export-pdf').hidden = !job.filtered_count;
}
// Asks how many flights to keep per destination, then downloads.
// A page loaded before the dialog existed keeps the plain download link.
if ($('export-dialog')) {
$('export-pdf').addEventListener('click', event => {
  event.preventDefault();
  try { $('export-count').value = localStorage.getItem('exportCount') || 3; } catch (error) {}
  $('export-dialog').showModal();
  $('export-count').select();
});
$('export-cancel').addEventListener('click', () => $('export-dialog').close());
$('export-dialog').addEventListener('submit', () => {
  const count = $('export-count').value;
  try { localStorage.setItem('exportCount', count); } catch (error) {}
  const href = $('export-pdf').getAttribute('href');
  location.href = href + (href.includes('?') ? '&' : '?') + 'per_city=' + encodeURIComponent(count);
});
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
const wide = window.matchMedia('(min-width: 1000px)');
const syncFiltersBox = () => { $('filters-box').open = wide.matches; };
wide.addEventListener('change', syncFiltersBox); syncFiltersBox();

// Highlights
function recommendationCard(title, trip, explanation, kind) {
  if (!trip) return `<div class="recommendation unavailable"><span class="recommendation-label">${title}</span><p>${explanation}</p></div>`;
  const timeText = trip.useful_minutes != null ? `<span class="recommendation-time">${duration(trip.useful_minutes)} ${trip.destination_airport_change ? 'tra i voli' : 'a destinazione'}</span>` : '';
  return `<button type="button" class="recommendation ${kind}" data-trip="${escapeHTML(trip.id)}"><span class="recommendation-label">${title}</span><span class="recommendation-city">${flag(trip.destination)}${escapeHTML(airportInfo(trip.destination).city)}</span><span class="recommendation-price">${money(trip.price)}</span><span class="recommendation-dates">${shortDay(trip.date)}${trip.return_date ? ' – '+shortDay(trip.return_date) : ''} · da ${escapeHTML(airportInfo(trip.origin).city)}</span>${timeText}<span class="recommendation-reason">${explanation}</span></button>`;
}
function renderRecommendations(job) {
  const r = job.recommendations, round = job.request.trip_type !== 'one_way';
  $('recommendations-panel').hidden = !r.cheapest;
  $('recommendation-method').hidden = !round;
  $('recommendations').innerHTML = recommendationCard('Il più economico',r.cheapest,'Il prezzo più basso trovato.','cheapest') +
    (round ? recommendationCard('Più tempo a destinazione',r.longest,r.longest?.id === r.cheapest?.id ? 'Ed è anche il più economico.' : r.longest ? 'Il soggiorno più lungo.' : 'Nessun confronto possibile.','longest') +
    recommendationCard('Il miglior compromesso',r.balanced,r.balanced ? `${money(r.balanced.extra_price)} in più, ${duration(r.balanced.extra_minutes)} in più sul posto.` : 'Il più economico è già la scelta migliore.','balanced') : '');
  $('recommendations').classList.toggle('single', !round);
}
function renderExploration(job) {
  renderRecommendations(job);
  const grouped = resultView === 'destinations';
  $('view-destinations').setAttribute('aria-pressed',String(grouped));
  $('view-trips').setAttribute('aria-pressed',String(!grouped));
  $('back-destinations').hidden = !$('filter-destination').value;
  $('destination-groups').hidden = !grouped;
  $('results').hidden = grouped;
  $('result-pagination').hidden = grouped || !job.has_more && offset === 0;
  const running = ['running','cancelling'].includes(job.status);
  const empty = job.result_count
    ? '<div class="empty-results"><h3>Nessun viaggio con questi filtri</h3><p>Prova ad alzare il prezzo massimo o ad azzerare i filtri.</p><button type="button" data-reset-results>Azzera filtri</button></div>'
    : running
      ? '<div class="empty-results searching"><span class="spinner" aria-hidden="true"></span><h3>Stiamo cercando…</h3><p>I primi voli compaiono qui appena li troviamo.</p></div>'
      : '<div class="empty-results"><h3>Nessun volo trovato</h3><p>Prova altre date, aggiungi aeroporti vicini, accetta uno scalo o alza il budget.</p></div>';
  // Filter on every airport of the city in the full pool, not only those left by the filters.
  const cityValues = new Map(cityOptions(job.facets.destinations).map(([value]) => [airportInfo(value.split(',')[0]).city, value]));
  $('destination-groups').innerHTML = job.groups.length ? job.groups.map(group => {
    const t = group.featured, codes = group.destinations;
    const airports = codes.length === 1 ? airportInfo(codes[0]).name : `${codes.length} aeroporti · ${codes.join(', ')}`;
    return `<button type="button" class="destination-card" data-destination="${escapeHTML(cityValues.get(group.city) || group.destination)}"><span class="destination-name"><strong>${flag(codes[0])}${escapeHTML(group.city)}</strong><small>${escapeHTML(airports)}</small></span><span class="destination-price"><small>da</small>${money(group.cheapest.price)}</span><span class="destination-best">${shortDay(t.date)}${t.return_date ? ' – '+shortDay(t.return_date) : ''} · da ${escapeHTML(airportInfo(t.origin).city)}</span><span class="destination-more">${countLabel(group.count,'opzione','opzioni')} →</span></button>`;
  }).join('') : empty;
  $('results').innerHTML = job.results.length ? job.results.map(card).join('') : empty;
  $('prev-page').disabled = offset === 0; $('next-page').disabled = !job.has_more;
  $('page-label').textContent = job.filtered_count ? `${offset+1}–${offset+job.results.length} di ${job.filtered_count}` : '';
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

// Trip cards
function airlineLogo(segment) {
  const code = segment.airline_code, name = segment.airline_name || code;
  const image = /^[A-Z0-9]{2}$/.test(code) ? `<img class="airline-logo" src="https://www.gstatic.com/flights/airline_logos/70px/${encodeURIComponent(code)}.png" alt="" width="32" height="32" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : '';
  return `<span class="airline-mark" title="${escapeHTML(name)}"><span class="airline-initial" aria-hidden="true">${escapeHTML(name.slice(0,1))}</span>${image}</span>`;
}
function leg(quote, title) {
  const first = quote.segments[0], last = quote.segments.at(-1), stops = quote.segments.length-1;
  const airlines = [...new Set(quote.segments.map(s => s.airline_name || s.airline_code))].join(', ');
  const numbers = quote.segments.map(s => `${s.airline_code} ${s.flight_number}`).join(' + ');
  const nextDay = last.arrival.slice(0,10) !== first.departure.slice(0,10);
  const via = stops ? quote.segments.slice(0,-1).map(seg => airportInfo(seg.to_code).city).join(', ') : '';
  return `<div class="leg">
    <div class="leg-airline">${airlineLogo(first)}</div>
    <div class="leg-body">
      <div class="leg-label">${title} · ${shortDay(first.departure)}</div>
      <div class="leg-times">
        <div class="leg-point"><strong>${localTime(first.departure)}</strong><span title="${escapeHTML(airportInfo(first.from_code).name)}">${escapeHTML(first.from_code)}</span></div>
        <div class="leg-line"><span>${duration(quote.duration_min)}</span><i aria-hidden="true"></i><span class="${stops ? 'stops' : 'nonstop'}">${stops ? countLabel(stops,'scalo','scali') + (via ? ' · ' + escapeHTML(via) : '') : 'Diretto'}</span></div>
        <div class="leg-point"><strong>${localTime(last.arrival)}${nextDay ? '<sup>+1</sup>' : ''}</strong><span title="${escapeHTML(airportInfo(last.to_code).name)}">${escapeHTML(last.to_code)}</span></div>
      </div>
      <div class="leg-meta">${escapeHTML(airlines)} · ${escapeHTML(numbers)}${stops && quote.self_transfer === true ? ' · <b>cambio volo a carico tuo</b>' : ''}</div>
    </div>
  </div>`;
}
function card(pair) {
  const outbound = pair.outbound, first = outbound.segments[0], last = outbound.segments.at(-1);
  const origin = first.from_code, dest = last.to_code, round = !!pair.inbound;
  const away = round ? pair.inbound.segments[0].from_code : null;
  const home = round ? pair.inbound.segments.at(-1).to_code : null;
  const checked = new Date(pair.checked_at).toLocaleTimeString('it-IT',{hour:'2-digit',minute:'2-digit'});
  const people = outbound.travelers;
  const link = (url, label, cls) => url ? `<a class="${cls}" href="${escapeHTML(safeLink(url))}" target="_blank" rel="noopener noreferrer">${label}</a>` : '';
  const primary = round ? (pair.round_trip_url || outbound.booking_url) : outbound.booking_url;
  // A cheaper single return ticket with this outbound: a hint, its return may differ.
  const single = pair.round_trip_from;
  const tags = [
    round ? `<span class="tag stay">${duration(pair.useful_minutes)} ${dest === away ? 'a destinazione' : 'tra i voli'}</span>` : '',
    round ? `<span class="tag">2 biglietti separati</span>` : '',
    {cabin:'<span class="tag">Trolley incluso</span>',checked:'<span class="tag">Valigia inclusa</span>'}[outbound.baggage] || '',
  ].join('');
  return `<article class="trip" data-id="${escapeHTML(pair.id)}">
    <div class="trip-main">
      <div class="trip-head"><h3>${escapeHTML(airportInfo(origin).city)} → ${flag(dest)}${escapeHTML(airportInfo(dest).city)}</h3><span>${round ? countLabel(pair.nights,'notte','notti') : 'Solo andata'}${round && home !== origin ? ' · rientro a ' + escapeHTML(airportInfo(home).city) : ''}</span></div>
      ${leg(outbound,'Andata')}${round ? leg(pair.inbound,'Ritorno') : ''}
      ${round && dest !== away ? `<p class="notice">Arrivi a ${escapeHTML(airportInfo(dest).name)} e riparti da ${escapeHTML(airportInfo(away).name)}: il trasferimento è a carico tuo.</p>` : ''}
      <div class="tags">${tags}</div>
    </div>
    <div class="trip-side">
      <div class="trip-price"><strong>${money(pair.price)}</strong><span>${round ? 'andata e ritorno' : 'solo andata'}${people > 1 ? `, ${people} persone` : ''}</span></div>
      ${single ? `<p class="single-ticket" title="Prezzo di Google per un solo biglietto di andata e ritorno con questo volo di andata. Il ritorno si sceglie su Google e può non essere quello mostrato qui.">Con questa andata Google vende un biglietto A/R unico da ${money(single)}, ma con un ritorno che può essere diverso da questo.</p>` : ''}
      ${link(primary, 'Vedi offerta', 'primary-link')}
      ${round ? `<div class="leg-links">${link(outbound.booking_url,'Solo andata','text-link')}${link(pair.inbound.booking_url,'Solo ritorno','text-link')}</div>` : ''}
      <div class="price-age ${pair.needs_recheck ? 'stale' : ''}">${pair.needs_recheck ? 'Prezzo da aggiornare' : 'Prezzo delle ' + checked}<button type="button" class="recheck">Aggiorna</button></div>
      <div class="recheck-output" role="status"></div>
    </div>
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
  button.disabled = true; output.textContent = 'Aggiorno il prezzo…';
  $('result-feedback').textContent = '';
  try {
    const data = await api(`/api/search/jobs/${jobId}/results/${article.dataset.id}/recheck`,'POST',{});
    $('result-feedback').textContent = data.status === 'confirmed'
      ? (data.price_changed ? `Il prezzo è cambiato: ora ${money(data.result.price)}.` : 'Prezzo confermato.')
      : data.message;
  } catch(e) { $('result-feedback').textContent = `Non è stato possibile aggiornare il prezzo: ${e.message}`; }
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
// A shared or bookmarked link wins over the last search in this browser.
jobId = new URLSearchParams(location.search).get('ricerca');
if (!jobId) try { jobId = localStorage.getItem('flight-finder-job'); } catch { /* storage optional */ }
if (jobId) poll();
// Restarts the portal, waits for it to answer again and reloads the page.
if ($('restart')) $('restart').addEventListener('click', async () => {
  const button = $('restart');
  button.disabled = true; button.textContent = 'Riavvio…';
  try {
    const response = await fetch('/api/restart', {method: 'POST'});
    if (!response.ok) throw new Error('restart');
  } catch (error) {
    button.disabled = false; button.textContent = 'Riavvio non riuscito';
    return;
  }
  await new Promise(resolve => setTimeout(resolve, 1200));
  for (let attempt = 0; attempt < 60; attempt++) {
    try { if ((await fetch('/api/health', {cache: 'no-store'})).ok) { location.reload(); return; } } catch (error) {}
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  button.disabled = false; button.textContent = 'Riavvio non riuscito';
});
