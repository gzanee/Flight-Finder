# API del motore unico

La pagina `/` gestisce date fisse/flessibili e sola andata/A/R. `/classic`
e `/weekends` reindirizzano a `/`. Nessuna modalità usa un secondo motore.

## Pianifica e avvia

- `POST /api/search/plan`: valida e restituisce `request` normalizzata e `plan`.
- `POST /api/search/jobs`: avvia la stessa richiesta; `202 {"id":"..."}`.
- `GET /api/search/jobs/<id>`: stato, risultati e calendario dei minimi.
- `POST /api/search/jobs/<id>/cancel`: ferma dopo la richiesta in corso.
- `POST /api/search/jobs/<id>/resume`: riprende dal progresso salvato.
- `POST /api/search/jobs/<id>/results/<result_id>/recheck`: prezzi freschi degli stessi voli.

Tutti gli indirizzi `/api/flexible/*` corrispondenti sono alias sullo stesso
manager, database, worker e limitatore.

### Esempio Weekend

```json
{
  "trip_type": "round_trip",
  "date_mode": "flexible",
  "origins": ["VCE", "TSF"],
  "destinations": [],
  "start_date": "2026-10-01",
  "end_date": "2026-12-31",
  "departure_days": [4],
  "return_days": [6],
  "departure_min": "18:00",
  "departure_max": "23:59",
  "return_min": "18:00",
  "return_max": "23:59",
  "min_nights": 2,
  "max_nights": 2,
  "budget": 100,
  "max_stops": 0,
  "final_arrival_max": "23:59"
}
```

### Esempio sola andata su data fissa

```json
{
  "trip_type": "one_way",
  "date_mode": "fixed",
  "origins": ["FCO", "CIA"],
  "destinations": ["MXP", "BGY"],
  "start_date": "2026-10-01",
  "departure_min": "00:00",
  "departure_max": "23:59",
  "budget": 150
}
```

Una sola andata fissa non richiede `end_date`. Le altre modalità richiedono
inizio e fine. Date passate, periodo invertito o oltre un anno sono rifiutati.
In date fisse i vincoli su giorni della settimana e notti non si applicano.
In flessibile il ritorno deve stare entro `end_date`.

## Parametri

| Campo | Significato |
| --- | --- |
| `trip_type` | `round_trip` (default) o `one_way` |
| `date_mode` | `flexible` (default) o `fixed` |
| `origins` | Lista o stringa di codici IATA, 1–10, default VCE+TSF |
| `destinations` | Lista/stringa IATA, massimo 200; vuoto = Ovunque |
| `destination_set` | Per Ovunque: `real` (default), `europe-short`, `europe-wide`, `sun`, `city-break`, `long-haul` |
| `start_date`, `end_date` | ISO YYYY-MM-DD, date esatte o estremi del periodo |
| `departure_days`, `return_days` | Giorni da 0=lunedì a 6=domenica; default venerdì/domenica |
| `min_nights`, `max_nights` | 0–30 notti di calendario, default 2; 0 = stesso giorno |
| `departure_min/max`, `return_min/max` | HH:MM inclusivi; default 18:00–23:59 |
| `departure_arrival_min/max`, `return_arrival_min/max` | Finestra locale di arrivo, default 00:00–23:59; ammette un giorno successivo |
| `final_arrival_max` | HH:MM: termine entro il giorno del ritorno, o di partenza per sola andata |
| `budget` | Massimo totale EUR per tutti i viaggiatori, 1–100000; default 100 |
| `adults`, `children`, `infants` | Default 1/0/0. Massimo 9 in totale; neonati in braccio non più degli adulti |
| `seat` | `economy`, `premium-economy`, `business`, `first` |
| `baggage` | `base`, `cabin`, `checked`: filtro Google per persone con posto |
| `exclude_basic_economy` | Booleano; filtro inviato a Google |
| `airline_mode` | `all` oppure `low_cost`, selezione esplicita di codici |
| `airlines` | Lista/stringa di codici IATA di due caratteri; filtro su ogni segmento |
| `max_stops` | 0–3, default 0 |
| `allow_self_transfer` | Default false; esclude anche la protezione sconosciuta |
| `allow_airport_change` | Default false; preferenza separata per cambio aeroporto nello scalo |
| `schengen_transfers_only` | Default false; ogni aeroporto di coincidenza deve appartenere a un paese Schengen |
| `min_layover_minutes`, `max_layover_minutes` | 0–4320, default 0/4320; calcolati in UTC |
| `departure_duration_min/max`, `return_duration_min/max` | Minuti complessivi incluse coincidenze; default 0/4320 |
| `return_airport` | `same` o `group`: stesso aeroporto o qualsiasi origine selezionata |
| `return_departure` | `same` o `group`: stesso arrivo o qualsiasi destinazione esplicita; gruppo non consentito in Ovunque |
| `min_destination_hours` | 0–720; minimo fra arrivo e ripartenza, default 0 |

Gli orari sono locali all’aeroporto. Durate e coincidenze vengono calcolate
in UTC per rispettare fusi orari e cambio dell’ora. Un orario locale ambiguo
o inesistente viene escluso. Un codice sconosciuto invalida l’intero gruppo,
anziché essere ignorato silenziosamente.

## Piano

`date_pairs` contiene `[andata, ritorno]`; in sola andata `ritorno` è null.
`destinations`, `candidate_count`, `coverage` descrivono il bacino.
`distinct_searches` conta le interrogazioni diverse; `searches_upper_bound`
include i ritorni possibili e le ripetizioni dopo la scadenza della cache.
`estimated_minutes` stima la sola attesa programmata; rete e retry si aggiungono.

Più di 50000 combinazioni o 100000 controlli possibili viene rifiutato prima
della scansione. Non ci sono tagli silenziosi. La copertura Ovunque parte dai
cataloghi disponibili e non è esaustiva. Con un’origine priva di catalogo,
il bacino generale è solo un insieme di candidati da verificare live.

## Avanzamento, risultati e calendario

Gli stati sono `running`, `cancelling`, `cancelled`, `blocked`, `interrupted`,
`complete`. La risposta include `completed`, `total`, `current`, `issues`,
`empty_searches`, `unverified`, `result_count` e `results` (pagine da 50).
Parametri GET: `offset` e `sort=price|useful|duration|departure`.

La consultazione ammette anche filtri sui soli risultati salvati:
`max_price` (totale EUR), `min_hours`, `origin`, `destination` (uno o più IATA
separati da virgole, ad esempio `LGW,LTN,STN` per Londra), `airline`
(codice IATA, deve coincidere su ogni segmento), `home_before` (HH:MM entro
il giorno di partenza del ritorno) ed `exclude` (destinazioni IATA separate
da virgole). Nessuno di questi parametri avvia chiamate a Google. Numeri non
finiti, negativi, codici e orari malformati restituiscono 400.

`result_count` resta il totale disponibile; `filtered_count` conta i viaggi
dopo i filtri. `facets` elenca origini, destinazioni e compagnie del bacino
completo. `groups` contiene una voce per città di destinazione con `city`,
`destinations` (aeroporti della città trovati), `destination` (gli stessi codici
separati da virgole, pronti per il filtro), `count`, `cheapest` (riepilogo più
economico) e `featured` (primo viaggio secondo l'ordinamento scelto). `airports`
mappa i codici a città e nome in italiano e a `country` (ISO 3166-1
alpha-2 minuscolo, per la bandiera in `static/flags/<country>.png`) (snapshot Wikidata in
`flightcore/data/airport_names_it.json`, rigenerabile con
`python -m flightcore.italian_names`).

`GET /api/search/jobs/<id>/export.pdf` restituisce gli stessi risultati come PDF
da scaricare (`voli-weekend.pdf`): una pagina di sintesi con una riga per città
e poi, per ogni città, fino a tre weekend diversi con orari, compagnia, prezzo,
ore sul posto e link a Google Flights. Accetta gli stessi filtri della
consultazione (non `sort`/`offset`) e non chiama Google.

`recommendations` contiene `cheapest` e, quando disponibili, `longest` e
`balanced`. Il compromesso richiede più tempo del più economico, non oltre
il maggiore tra 20 EUR e il 50% del suo prezzo in più, e al massimo 3 EUR
per ora guadagnata. Vince il costo per ora a destinazione più basso. Include
`extra_price` e `extra_minutes`; è assente quando non ci sono alternative
adatte. I cambi di aeroporto a destinazione sono esclusi dal confronto
del tempo. In sola andata compare solo `cheapest`.

Gruppi, suggerimenti e calendario sono calcolati dopo i filtri, su tutte le
pagine. `focus=<result_id>` restituisce la pagina contenente il suggerimento;
`focused_id` è null se il viaggio è stato escluso o rimosso. `offset` nella
risposta è quello effettivo, limitato all'ultima pagina esistente.

Un viaggio espone `price`, `currency`, `outbound`, `inbound`, `nights`,
`useful_minutes`, `checked_at`, `needs_recheck`, `ticketing`, `verification`.
Per sola andata `inbound`, `nights` e `useful_minutes` sono null.
Per A/R il totale è la somma di due biglietti di sola andata.
Le tratte includono segmenti con identità dei voli, aeroporti, orari e prezzo.

`calendar` aggrega i risultati validi che superano i filtri, prima della paginazione:
`{date, return_date, price}`. Una data mancante non dimostra indisponibilità.
Il calendario non esegue ulteriori ricerche e riflette anche i ricontrolli.

`destination_airport_change` segnala un arrivo diverso dall’aeroporto di
ripartenza. Il prezzo non include il trasporto a terra; `useful_minutes`
misura solo il tempo fra i due voli, senza sottrarre quel trasferimento.

`google_live` attesta una quotazione osservata su Google, non la disponibilità
diretta della compagnia. `airline_verified` è false. Bagagli/classe sono
filtri del confronto; sconti di abbonamenti personali non vengono applicati.

## Ricontrollo e ripresa

Il ricontrollo cerca lo stesso numero volo, aeroporti, date e orari su una o
due tratte secondo il viaggio. Risponde `confirmed` con `result` e
`price_changed`, oppure `not_confirmed` / `outside_filters`; i risultati non
confermati sono esclusi dalle pagine successive. Gli errori di rete non
rimuovono un prezzo precedente come se fosse certamente indisponibile.

Il database locale è `.cache/weekends.sqlite3`. I lavori della versione
precedente si caricano con i nuovi campi ai valori di default. Il riavvio
richiede Riprendi. C’è un solo worker per processo per tutte le modalità.

Le richieste sono seriali con 0,3 secondi dopo ogni risposta; l’ambiente
`FLIGHT_FINDER_GOOGLE_INTERVAL` permette da 0,3 a 10 secondi. Cache 5 minuti;
max 2 retry per errori di rete. HTTP 403/429 ferma e attiva una pausa di 15
minuti. Una risposta sconosciuta non equivale a zero voli.

Errori HTTP: 400 parametri invalidi, 404 lavoro assente, 409 lavoro attivo o
ripresa non consentita, 503 ricontrollo non disponibile.

## Indirizzi precedenti e utilità

`POST /api/search`, `/api/explore`, `/api/cheapest-dates` restano ingressi di
compatibilità ma avviano lo stesso worker. Restituiscono **202** con `id` e
`status_url`, non più offerte sincrone. Per i nuovi client usare direttamente
`/api/search/plan` e `/api/search/jobs`.

La traduzione supporta `origin/origins`, `destination/destinations`,
`departure_date`, `return_date`, `max_price`, `seat`, passeggeri, compagnie,
`direct_only`, `max_stops`, le vecchie finestre `*_time_min/max` e le rose di
destinazioni. Il vecchio calendario usa `from_date`, `to_date`, `trip_days`.
I minimi si leggono poi nel campo `calendar` del lavoro.

- `GET /api/airports?q=...`: autocomplete offline, codice/nome/alias italiani.
- `GET /api/health`: motore unico, librerie, intervallo e pausa residua.
- `DELETE /api/cache`: svuota la cache prezzi, conservando un blocco Google attivo.
