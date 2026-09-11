# ✈️ Flight Finder — voli economici da VCE/TSF

Portale per trovare voli economici da Venezia Marco Polo (VCE) e Treviso Canova (TSF)
verso qualsiasi destinazione, con i dati di **Google Flights**.

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-3.1-green.svg)
![Nessuna_API_key](https://img.shields.io/badge/API%20key-non%20richiesta-brightgreen.svg)

## Perché Google Flights e non Amadeus

Questo progetto usava le API Amadeus Self-Service. Il problema è che Amadeus copre
male le compagnie low-cost: partendo da VCE/TSF significa non vedere Ryanair, Wizz
ed easyJet, cioè la quasi totalità dei voli economici dal Veneto.

Con i dati di Google Flights lo stesso weekend a Barcellona passa da "nessun
risultato utile" a **VCE→BCN a 40 €** con Ryanair. In più non serve registrarsi,
non ci sono chiavi API e non c'è un tetto mensile di chiamate.

## Funzionalità

- **Ricerca "ovunque"** — scandisce una rosa di destinazioni e mostra la migliore
  offerta per ciascuna, una riga per meta.
- **Ricerca su due aeroporti insieme** — VCE e TSF in un'unica query, risultati uniti
  e ordinati.
- **Filtri applicati da Google, non a posteriori** — fascia oraria di partenza e
  ritorno, numero di scali, tetto di prezzo, classe, compagnie.
- **Calendario prezzi** — quanto costa la stessa rotta giorno per giorno su 45 giorni.
- **Dati ricchi** — scali, tempo a terra, tipo di aereo, spazio per le gambe,
  emissioni di CO₂ confrontate con la media della rotta.
- **Autocomplete offline** — 7.835 aeroporti, con i nomi italiani ("londra", "zurigo").
- **Nessuna chiave API, nessuna registrazione.**

## Avvio rapido

Serve **Python 3.10 o superiore** (le librerie dei provider usano sintassi moderna).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Apri [http://localhost:5001](http://localhost:5001). Il file `.env` è opzionale:
senza, valgono i valori predefiniti (Italia, italiano, euro).

## Come funziona

Nessuna delle librerie disponibili è ufficiale né affidabile da sola, quindi
l'app ne usa **due in cascata** dietro un'interfaccia comune:

| Provider | Come ottiene i dati | Risultati tipici | Ruolo |
|---|---|---|---|
| [`fli`](https://github.com/punitarani/fli) | endpoint interno di Google | ~138 per rotta | primario |
| [`fast-flights`](https://github.com/AWeirdDev/flights) | HTML della pagina | ~8 per rotta | riserva |

`fli` restituisce molto di più — nei test ha trovato un British Airways diretto a
96 € che `fast-flights` non vedeva — ma il suo endpoint viene rifiutato da Google a
intermittenza. Quando succede, `fast-flights` subentra senza che l'utente se ne
accorga, e un **circuit breaker** smette di interrogare per 5 minuti il provider
che non risponde, invece di pagarne il timeout a ogni ricerca.

Lo stato dei provider è sempre visibile in alto a destra nella pagina e su
`GET /api/health`.

### Il consenso cookie, ovvero perché gli altri "non funzionano"

Da un IP europeo, ogni richiesta a Google Flights viene intercettata da
`consent.google.com` finché il client non possiede il cookie `SOCS`. Nessuna delle
librerie lo gestisce: ricevono la pagina di consenso, non trovano i dati e
**restituiscono zero risultati senza sollevare errori**. È la causa di buona parte
delle segnalazioni "funziona per tutti tranne che a me" nelle loro issue.

`flightcore/consent.py` esegue una volta il flusso *Reject all* (rifiuta i cookie di
profilazione: basta a ottenere `SOCS` ed è la scelta meno invasiva), mette il
risultato in cache su disco e lo inietta nei client HTTP dei due provider — che
usano librerie diverse, `curl_cffi` e `primp`, e vanno agganciati separatamente.

Senza questo passaggio l'intero portale restituisce zero risultati.

## Struttura

```
flight-finder/
├── app.py                    # rotte Flask e validazione delle richieste
├── flightcore/
│   ├── consent.py            # consenso cookie di Google + iniezione nei client
│   ├── engine.py             # catena di fallback, multi-origine, "ovunque"
│   ├── models.py             # modello normalizzato comune ai provider
│   ├── breaker.py            # circuit breaker per provider
│   ├── cache.py              # cache in memoria con scadenza
│   ├── airports.py           # catalogo IATA offline + alias italiani
│   └── providers/
│       ├── fli_provider.py           # primario
│       └── fastflights_provider.py   # riserva
├── templates/index.html      # interfaccia
├── tests/test_core.py        # test senza rete
└── docs/API.md               # documentazione degli endpoint
```

## API

| Endpoint | Metodo | Descrizione |
|---|---|---|
| `/api/search` | POST | Voli su una rotta, da uno o più aeroporti di partenza |
| `/api/explore` | POST | Ricerca "ovunque" su una rosa di destinazioni |
| `/api/cheapest-dates` | POST | Calendario prezzi su un intervallo di date |
| `/api/airports?q=` | GET | Autocomplete aeroporti (offline) |
| `/api/health` | GET | Stato dei provider e del consenso cookie |
| `/api/cache` | DELETE | Azzera cache e circuit breaker |

Parametri ed esempi completi in **[docs/API.md](docs/API.md)**.

## Test

```bash
python -m pytest tests/ -q
```

I test coprono circuit breaker, cache, normalizzazione dei risultati, catalogo
aeroporti e validazione delle richieste. Non fanno chiamate di rete: le ricerche
vere dipendono da Google e dal momento della giornata.

## Limiti da conoscere

- **Le librerie non sono ufficiali.** Google ha chiuso la Flights API pubblica nel
  2018; questi progetti fanno reverse engineering e possono smettere di funzionare
  senza preavviso. Va bene per uso personale, meno per un servizio pubblico — e
  formalmente è contrario ai Termini di servizio di Google.
- **Le raffiche di richieste vengono rifiutate.** L'app limita la concorrenza a 4
  richieste e mette in cache i risultati per 15 minuti proprio per questo. Se una
  ricerca torna vuota, spesso basta riprovare dopo qualche minuto.
- **In cloud funziona peggio.** Gli IP dei datacenter vengono bloccati molto più
  spesso di quelli residenziali: da locale la resa è nettamente migliore.
- **Sull'andata e ritorno** il prezzo mostrato è già il totale A/R, ma il volo di
  rientro si sceglie su Google dopo aver fissato l'andata. È come funziona la
  pagina originale, e l'interfaccia lo segnala esplicitamente.
- I prezzi sono indicativi, non includono bagagli extra e il totale si riferisce a
  tutti i passeggeri selezionati.

## Licenza

MIT. I provider hanno licenze proprie: vedi
[punitarani/fli](https://github.com/punitarani/fli) e
[AWeirdDev/flights](https://github.com/AWeirdDev/flights).

---

Creato con ❤️ per trovare voli economici dal Veneto
