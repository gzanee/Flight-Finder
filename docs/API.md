# API

Tutti gli endpoint POST accettano e restituiscono JSON (`Content-Type: application/json`).
Gli errori di validazione tornano `400` con `{"success": false, "error": "..."}`.

Base URL locale: `http://localhost:5001`

---

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
- **`inbound_pending: true`** significa: la ricerca era A/R, il prezzo è già quello
  A/R, ma il volo di rientro va scelto su Google dopo aver fissato l'andata. È il
  comportamento della pagina originale, non un dato mancante.
- **`duration_min`** di un leg include il tempo a terra negli scali.
- **`co2_delta_pct`** è lo scarto rispetto alla media della rotta: negativo = meglio.
- **`providers`** elenca chi ha effettivamente risposto. Se cambia fra una ricerca e
  l'altra, il primario era temporaneamente rifiutato da Google.
- **`flight_number` e `airline_code`** sono valorizzati solo dal provider `fli`:
  la pagina HTML da cui legge `fast-flights` non li espone.

---

## POST `/api/explore`

Ricerca "ovunque": scandisce una rosa di destinazioni e restituisce **la migliore
offerta per ciascuna**, una riga per meta. Accetta gli stessi filtri di
`/api/search`, più:

| Campo | Tipo | Default | Note |
|---|---|---|---|
| `destinations` | lista di IATA | — | Elenco esplicito, ha la precedenza |
| `destination_set` | chiave di rosa | `europe-short` | Vedi sotto |
| `max_destinations` | int 1–60 | `20` | Più mete = più lento |

Rose disponibili: `europe-short` (20), `europe-wide` (50), `sun` (30),
`city-break` (29), `long-haul` (30). L'elenco aggiornato con le etichette è in
`flightcore/airports.py` e viene passato alla pagina.

Le ricerche girano in parallelo con al massimo 4 richieste contemporanee: una
scansione di 12 mete su due aeroporti richiede circa 5 secondi.

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

- `consent_cookies` **deve contenere `SOCS`**. Se manca, il consenso cookie non è
  stato ottenuto e ogni ricerca tornerà vuota.
- `paused: true` indica un provider messo in pausa dal circuit breaker;
  `retry_in_s` dice fra quanto verrà riprovato.
- `failures` conta i guasti veri, `empties` le risposte valide ma senza offerte.
  Le seconde pesano meno: una rotta su cui non vola nessuno non è un guasto.

---

## DELETE `/api/cache`

Azzera la cache dei risultati (15 minuti) e riapre tutti i circuit breaker.
Utile per forzare una ricerca fresca o per far ritentare subito un provider in pausa.

```bash
curl -X DELETE http://localhost:5001/api/cache
```
