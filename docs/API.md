# API

Tutti gli endpoint POST accettano e restituiscono JSON (`Content-Type: application/json`).
Gli errori di validazione tornano `400` con un campo `error`.

Base URL locale: `http://localhost:5001`

---

## Weekend Finder e Flexible Trip Finder

La pagina principale usa questi endpoint. Gli endpoint storici descritti più
avanti appartengono a `/classic` e non garantiscono gli stessi controlli A/R.

### POST `/api/flexible/plan` e POST `/api/flexible/jobs`

Entrambi accettano lo stesso oggetto. `plan` valida e prepara la scansione senza
interrogare Google Flights; per Ovunque può aggiornare i candidati da
Wikipedia/Wikidata. `jobs` avvia la ricerca e risponde `202 {"id":"..."}`.

```json
{
  "origins": ["VCE", "TSF"],
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
  "return_airport": "same",
  "allow_self_transfer": false,
  "adults": 1,
  "children": 0,
  "baggage": "base",
  "airline_mode": "all",
  "final_arrival_max": "23:59",
  "min_destination_hours": 0
}
```

| Campo | Regole / valori |
|---|---|
| `origins` | VCE, TSF, VRN, BLQ, BGY; almeno una, default VCE + TSF |
| `destinations` | Assente o vuoto = Ovunque; altrimenti lista IATA o stringa separata da virgole/spazi, massimo 200 |
| `start_date`, `end_date` | Obbligatorie; inizio non nel passato; periodo massimo 366 giorni; entrambe le date del viaggio devono rientrare nel periodo |
| `departure_days`, `return_days` | Liste di interi: lunedì 0 … domenica 6; default venerdì 4 / domenica 6 |
| `departure_min/max`, `return_min/max` | HH:MM nello stesso giorno, inclusivi al minuto; default 18:00–23:59 |
| `min_nights`, `max_nights` | 1–30, default 2; differenza tra le date di partenza delle due tratte, non notti in hotel |
| `budget` | Totale EUR per tutti i viaggiatori e le due tratte; positivo, massimo 100000, default 100 |
| `adults`, `children` | Almeno un adulto, massimo 9 persone; bambini con posto, niente neonati in questa modalità |
| `max_stops` | 0 (default) o 1 per ciascuna tratta |
| `allow_self_transfer` | Default false; true include anche coincidenze la cui protezione non è dichiarata |
| `return_airport` | `same` (default) oppure `group` |
| `airline_mode` | `all` (default) oppure `low_cost`, elenco esplicito nel modulo flexible |
| `baggage` | `base` (default), `cabin`, `checked`: filtri Google, non attestazione del bagaglio incluso dal vettore |
| `final_arrival_max` | Facoltativo HH:MM: arrivo a casa entro quell'ora nel giorno scelto per il ritorno |
| `min_destination_hours` | 0–720, default 0; minimo tempo reale tra arrivo dell'andata e partenza del ritorno |

Il piano restituisce `request` normalizzata e `plan` con:

- `date_pairs`: tutte le coppie che rispettano giorni e notti;
- `destinations`, `candidate_count`, `coverage`: candidati e provenienza;
- `distinct_searches`: interrogazioni differenti;
- `searches_upper_bound`: massimo di chiamate per una scansione senza
  arresti, contando anche le tratte ripetute in caso di scadenza della cache;
- `estimated_minutes`: tempo a quel massimo con l'intervallo configurato
  (1,5 secondi di default);
  i tempi di rete si aggiungono, le cache e le andate fuori filtro riducono il lavoro.

Più di 50000 combinazioni genera un errore esplicito: il piano non viene
troncato. Ovunque usa l'unione dei cataloghi delle origini come candidati;
la copertura non è esaustiva, soprattutto per rotte nuove o con scalo.

### GET `/api/flexible/jobs/<id>`

Parametri facoltativi: `sort=price|useful`, `offset=0`. Restituisce 50
risultati per pagina e `has_more`. Lo stato contiene `completed`, `total`,
`current`, `issues`, `result_count`, `unverified`, `request`, `plan`
e `results`. `unverified` conta le righe scartate per dati insufficienti,
non un conteggio univoco di voli.

Stati: `running`, `cancelling`, `cancelled`, `blocked`,
`interrupted`, `complete`. Complete significa che tutti i candidati
del piano sono stati esaminati, non copertura di tutti i voli esistenti.
Blocked/interrupted non equivalgono a nessuna disponibilità.

Ogni risultato contiene `outbound` e `inbound`, entrambe con `segments`,
prezzo, valuta, viaggiatori, bagaglio, `checked_at` e link di ricerca Google.
Ogni segmento ha aeroporti, compagnia, numero volo e orari ISO con fuso.
I campi comuni sono:

- `price`: somma dei due prezzi, non prezzo della sola andata;
- `ticketing: "separate_one_way"`: due biglietti distinti;
- `nights`: giorni di calendario tra partenza e ritorno;
- `useful_minutes`: arrivo a destinazione → ripartenza, calcolato in UTC
  anche durante i cambi di ora;
- `verification: "google_live"`, `airline_verified: false`: dati di
  shopping osservati su Google, senza conferma diretta del vettore;
- `needs_recheck`: true dopo cinque minuti dalla quotazione più vecchia.

Gli orari locali ambigui o inesistenti al cambio di ora vengono scartati
se la fonte non consente di determinarne il fuso senza ambiguità.

### Arresto, ripresa e ricontrollo

- `POST /api/flexible/jobs/<id>/cancel`: richiede l'arresto tra due chiamate.
- `POST /api/flexible/jobs/<id>/resume`: riprende il lavoro salvato; risponde 202.
- `POST /api/flexible/jobs/<id>/results/<result_id>/recheck`: rilegge
  entrambe le tratte saltando la cache e cerca gli stessi numeri di volo,
  aeroporti e orari. Restituisce `confirmed` con il nuovo risultato e
  `price_changed`, oppure `not_confirmed` / `outside_filters`.
  Le coppie non confermate sono escluse dalle successive pagine di risultati.

Errori: 400 filtri invalidi, 404 lavoro/risultato assente, 409 lavoro già in
corso o ripresa non consentita, 503 ricontrollo indisponibile. La forma
dell'errore è `{"error":"..."}`, con `status: "unavailable"` nel 503.

La persistenza usa SQLite locale. Un processo gestisce un solo lavoro attivo.
Il riavvio non rilancia la scansione: lo stato è presentato come interrupted e
l'utente può riprendere. Le richieste Google sono seriali e distanziate di 1,5
secondi per default; `FLIGHT_FINDER_GOOGLE_INTERVAL` consente di scegliere un
intervallo fra 1 e 10 secondi. Un reset o timeout di rete transitorio viene
ritentato al massimo due volte con attesa crescente. Un 403/429 ferma subito
il lavoro e attiva un cooldown di 15 minuti nel processo.

---

## API precedenti (`/classic`)

Il loro formato rimane disponibile per compatibilità. Un'offerta con ritorno
non selezionato non soddisfa la verifica A/R del Weekend Finder.

## POST `/api/search`

Cerca voli su una rotta precisa, eventualmente da più aeroporti di partenza.
Se `destination` è assente o vale `"anywhere"`, la richiesta viene trattata come
[`/api/explore`](#post-apiexplore).

### Parametri

| Campo | Tipo | Default | Note |
|---|---|---|---|
| `origins` | lista di IATA | `["VCE"]` | Accetta anche `origin` come stringa singola |
| `destination` | IATA | — | Vuoto ⇒ ricerca "ovunque" |
| `departure_date` | `AAAA-MM-GG` | fra 14 giorni | Non può essere nel passato |
| `return_date` | `AAAA-MM-GG` | `null` | Assente ⇒ solo andata |
| `adults` | int 1–9 | `1` | |
| `children` | int 0–8 | `0` | |
| `infants` | int 0–8 | `0` | |
| `seat` | `economy` \| `premium-economy` \| `business` \| `first` | `economy` | |
| `max_stops` | int 0–3 | — | `0` = solo diretti |
| `direct_only` | bool | `false` | Scorciatoia per `max_stops: 0` |
| `max_price` | int | — | Tetto sul **totale** dell'offerta |
| `departure_time_min` | ora 0–23 o `"18:00"` | — | Parte non prima di |
| `departure_time_max` | ora 0–23 | — | Parte non dopo |
| `arrival_time_min` / `arrival_time_max` | ora 0–23 | — | Sull'arrivo dell'andata |
| `return_time_min` / `return_time_max` | ora 0–23 | — | Sulla partenza del ritorno |
| `airlines` | lista di codici IATA | — | Es. `["FR", "VY"]` |
| `exclude_basic_economy` | bool | `false` | |
| `sort_by` | `cheapest` \| `best` \| `duration` \| `departure` \| `emissions` | `cheapest` | |

Gli orari sono **ore intere**: Google filtra per ora, non per minuto. `"18:30"` viene
interpretato come `18`.

### Esempio

```bash
curl -X POST http://localhost:5001/api/search \
  -H 'Content-Type: application/json' \
  -d '{
    "origins": ["VCE", "TSF"],
    "destination": "BCN",
    "departure_date": "2026-11-06",
    "return_date": "2026-11-09",
    "max_stops": 0,
    "max_price": 200,
    "departure_time_min": 17,
    "return_time_min": 15
  }'
```

### Risposta

```json
{
  "success": true,
  "mode": "route",
  "count": 4,
  "origins": ["VCE", "TSF"],
  "destination": "BCN",
  "destination_name": "Barcelona International Airport",
  "providers": ["fast-flights"],
  "flights": [
    {
      "price": 60.0,
      "currency": "EUR",
      "origin": "VCE",
      "destination": "BCN",
      "provider": "fast-flights",
      "stops": 0,
      "duration_min": 115,
      "airlines": ["Ryanair"],
      "co2_kg": 73.0,
      "co2_delta_pct": -12,
      "self_transfer": false,
      "round_trip": true,
      "inbound_pending": true,
      "booking_url": "https://www.google.com/travel/flights/search?tfs=...",
      "tags": [],
      "outbound": {
        "departure": "2026-11-06T08:40:00",
        "arrival": "2026-11-06T10:35:00",
        "duration_min": 115,
        "stops": 0,
        "layovers": [],
        "from_code": "VCE",
        "to_code": "BCN",
        "airlines": ["Ryanair"],
        "segments": [
          {
            "from_code": "VCE", "to_code": "BCN",
            "from_name": "Aeroporto di Venezia Marco Polo",
            "to_name": "Aeroporto di Barcellona",
            "departure": "2026-11-06T08:40:00",
            "arrival": "2026-11-06T10:35:00",
            "duration_min": 115,
            "airline_code": "FR", "airline_name": "Ryanair",
            "flight_number": "1234",
            "aircraft": "Boeing 737",
            "legroom": "74 cm",
            "overnight": false
          }
        ]
      },
      "inbound": null
    }
  ]
}
```

### Campi da leggere con attenzione

- **`price`** è il totale per tutti i passeggeri, non a persona.
- **`inbound_pending: true`** significa che il ritorno non è stato selezionato.
  Il prezzo iniziale A/R non prova che il rientro rispetti i filtri: usa la nuova
  API flexible quando serve una coppia completa con orari di ritorno verificati.
- **`duration_min`** di un leg include il tempo a terra negli scali.
- **`co2_delta_pct`** è lo scarto rispetto alla media della rotta: negativo = meglio.
- **`providers`** elenca chi ha effettivamente risposto. Se cambia fra una ricerca e
  l'altra, il primario era temporaneamente rifiutato da Google.
- **`flight_number` e `airline_code`** possono mancare nel parser classic di
  `fast-flights`. La nuova API flexible li legge dai dati strutturati della pagina
  ed esclude le offerte che ne sono prive.

---

## POST `/api/explore`

Ricerca "ovunque": scandisce una rosa di destinazioni e restituisce **la migliore
offerta per ciascuna**, una riga per meta. Accetta gli stessi filtri di
`/api/search`, più:

| Campo | Tipo | Default | Note |
|---|---|---|---|
| `destinations` | lista di IATA | — | Elenco esplicito, ha la precedenza |
| `destination_set` | chiave di rosa | `europe-short` | Vedi sotto |
| `max_destinations` | int 1–200 | `150` | Per aeroporto di partenza; più mete = più lento |

Rose disponibili: `europe-short` (20), `europe-wide` (50), `sun` (30),
`city-break` (29), `long-haul` (30). L'elenco aggiornato con le etichette è in
`flightcore/airports.py` e viene passato alla pagina.

I tempi dipendono dal numero di mete, dalla cache, dal limite delle richieste
e dalla risposta di Google. La scansione classic è sincrona; quella flexible
espone progresso e arresto/ripresa.

```bash
curl -X POST http://localhost:5001/api/explore \
  -H 'Content-Type: application/json' \
  -d '{"origins":["VCE","TSF"],"destination_set":"europe-short",
       "max_destinations":12,"departure_date":"2026-11-06","max_stops":0}'
```

La risposta ha `mode: "explore"`, il campo `searched_destinations` con le mete
effettivamente esaminate, e ogni volo porta in più `destination_name`.

---

## POST `/api/cheapest-dates`

Calendario prezzi: il minimo per ogni giorno dell'intervallo.

| Campo | Tipo | Default | Note |
|---|---|---|---|
| `origin` | IATA | `VCE` | Un solo aeroporto |
| `destination` | IATA | **obbligatorio** | |
| `from_date` | `AAAA-MM-GG` | fra 7 giorni | |
| `to_date` | `AAAA-MM-GG` | fra 60 giorni | |
| `trip_days` | int 1–30 | — | Presente ⇒ prezzi A/R per soggiorni di N giorni |
| `adults` | int 1–9 | `1` | |
| `max_stops` | int 0–3 | — | |

```json
{
  "success": true,
  "count": 46,
  "origin": "VCE",
  "destination": "BCN",
  "provider": "fli",
  "cheapest": 38.0,
  "dates": [{"date": "2026-10-01", "price": 52.0, "currency": "EUR", "return_date": null}]
}
```

> **Solo `fli` sa costruire il calendario prezzi.** Quando il suo endpoint è
> rifiutato da Google, la risposta è `count: 0` con `provider: null` — non un
> errore. L'interfaccia lo segnala e invita a riprovare più tardi.

---

## GET `/api/airports`

Autocomplete servito da un catalogo locale di 7.835 aeroporti: nessuna chiamata di
rete, nessuna chiave API.

```bash
curl 'http://localhost:5001/api/airports?q=londra'
```

```json
{"success": true, "airports": [
  {"code": "LHR", "name": "London Heathrow Airport", "home": false},
  {"code": "LGW", "name": "London Gatwick Airport", "home": false}
]}
```

Servono almeno 2 caratteri. La ricerca accetta il codice IATA, il nome
dell'aeroporto (senza badare agli accenti: `zurich` trova `Zürich`) e i nomi
italiani delle città tramite una tabella di alias — `londra`, `monaco`,
`cracovia`, `l'avana`. `home: true` marca VCE e TSF.

---

## GET `/api/health`

Stato del sistema. Utile quando una ricerca torna vuota.

```json
{
  "success": true,
  "providers": ["fli", "fast-flights"],
  "breaker": {
    "fli": {"failures": 3, "empties": 0, "paused": true,
            "retry_in_s": 240, "last_error": "nessun risultato"}
  },
  "consent_cookies": ["SOCS", "__Secure-ENID"],
  "country": "IT",
  "currency": "EUR"
}
```

- `consent_cookies` elenca solo i nomi dei cookie ottenuti. Se Google richiede
  il consenso e `SOCS` manca, la ricerca può ricevere una pagina senza voli.
- `paused: true` indica un provider messo in pausa dal circuit breaker;
  `retry_in_s` dice fra quanto verrà riprovato.
- `failures` conta i guasti veri, `empties` le risposte valide ma senza offerte.
  Le seconde pesano meno: una rotta su cui non vola nessuno non è un guasto.

---

## DELETE `/api/cache`

Questo endpoint riguarda il motore classic. Non elimina i lavori flexible e
non azzera il loro cooldown: per un prezzo flexible aggiornato usa `recheck`.

Azzera la cache dei risultati (15 minuti) e riapre tutti i circuit breaker.
Utile per forzare una ricerca fresca o per far ritentare subito un provider in pausa.

```bash
curl -X DELETE http://localhost:5001/api/cache
```
