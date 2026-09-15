# Flight Finder · Weekend Finder

Portale locale per trovare viaggi A/R economici da **Venezia, Treviso, Verona,
Bologna e Bergamo**, con dati di Google Flights e filtri precisi su giorni e orari.

## Cosa puoi cercare

La pagina principale apre **Weekend Finder** con venerdì dopo le 18,
ritorno domenica dopo le 18, VCE + TSF, 2 notti, voli diretti e budget
totale di 100 €. **Flexible Trip Finder** permette altre combinazioni di
giorni e durate, per esempio giovedì–domenica oppure 3–5 notti.

- Origini multiple e destinazioni Ovunque o aeroporti specifici.
- Periodo di ricerca e giorni della settimana distinti per andata e ritorno.
- Fasce orarie locali al minuto, applicate di nuovo ai voli restituiti.
- Notti di calendario, con entrambi i giorni di viaggio compresi nel periodo.
- Ritorno nello stesso aeroporto oppure in uno qualunque del gruppo selezionato.
- Budget totale per tutti i viaggiatori, adulti e bambini con posto.
- Diretti o massimo uno scalo; self-transfer come preferenza separata.
- Tutte le compagnie o una selezione esplicita di low-cost; filtro tariffa base,
  trolley o stiva.
- Arrivo finale entro un orario nel giorno di ritorno, per escludere arrivi
  dopo mezzanotte; minimo tempo utile a destinazione.
- Ordinamento per prezzo A/R o tempo utile; ricontrollo dei voli selezionati.

## Avvio

Versioni verificate con **Python 3.12**.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Apri [il portale locale](http://localhost:5001/). La nuova interfaccia è anche
su `/weekends`; quella precedente rimane su `/classic`.

## Che cosa significa un risultato valido

Il motore cerca **due tratte di sola andata** e ne somma i prezzi. Ogni
risultato contiene entrambe le tratte, con data, aeroporti, numero di volo,
compagnia, orari e prezzo. Vengono escluse le righe prive di identità o prezzo
live, i voli fuori filtro e i collegamenti di cui non è nota la protezione
quando il self-transfer è disabilitato.

Il risultato è una combinazione di **due biglietti separati**: non è una
tariffa A/R unica emessa dal vettore. Il prezzo copre tutti i viaggiatori
selezionati. I bagagli sono un filtro del confronto Google; peso, dimensioni,
inclusione esatta e prezzo finale vanno verificati dal venditore.

`verification: google_live` significa che Google ha restituito una
quotazione con dati di shopping nel momento indicato da `checked_at`.
**Non è una conferma di emissione o disponibilità diretta della compagnia**:
`airline_verified` resta `false`. Il portale non prenota né compra biglietti.

Dopo cinque minuti il prezzo è marcato da ricontrollare. Il pulsante dedicato
ricerca nuovamente gli stessi voli su entrambe le tratte; aggiorna il totale
oppure rimuove dalle offerte valide la coppia che non viene confermata.
Un errore di rete viene segnalato come controllo non disponibile.

## Ovunque, cataloghi e ricerche lunghe

Il catalogo di aeroporti serve a riconoscere i codici IATA. Le tabelle di rotte
di Wikipedia, risolte tramite Wikidata e conservate in uno snapshot locale,
forniscono **destinazioni candidate**. Non sono un calendario operativo e
non dimostrano che un volo sia vendibile in una data specifica.

Ovunque controlla l'unione delle destinazioni candidate da tutte le origini
scelte. La copertura **non è esaustiva**: può mancare una nuova rotta o una
meta raggiungibile con scalo. La scelta di aeroporti specifici permette di
cercare anche fuori dal catalogo di rotte.

Prima di partire la pagina mostra combinazioni e quantità massima di
controlli per una scansione completa. Un trimestre su molti aeroporti può
richiedere ore: ogni tratta richiede una ricerca, e Google può limitarle.
Le richieste sono sequenziali, distanziate di 1,5 secondi per impostazione
predefinita nel nuovo motore, con cache di cinque minuti. L'intervallo resta
configurabile fra 1 e 10 secondi con `FLIGHT_FINDER_GOOGLE_INTERVAL`. Un reset
o timeout di rete transitorio viene ritentato al massimo due volte con attesa crescente. Un 403/429
interrompe la ricerca, conserva il progresso e impone una pausa di quindici
minuti nel processo.

I lavori sono salvati in `.cache/weekends.sqlite3`: puoi fermarli e
riprenderli, anche dopo un riavvio del server. Il lavoro attivo procede
finché il processo è acceso; non riparte automaticamente dopo un riavvio.
Il percorso della cache è configurabile con `FLIGHT_FINDER_CACHE_DIR`
nell'ambiente del processo. La pagina ripristina l'ultima ricerca del browser.

## Architettura

- `flightcore/flexible.py`: validazione, combinazioni, fusi orari e filtri.
- `flightcore/live_quotes.py`: query di **fast-flights** e decoder di **fli**,
  con controlli sul formato e sull'identità dei voli.
- `flightcore/flexible_jobs.py`: lavori persistenti, arresto, ripresa e ricontrollo.
- `flightcore/flexible_api.py`: API e pagina principale.
- `templates/weekends.html` e `static/weekends.*`: interfaccia senza CDN.
- `flightcore/consent.py`: consenso Google, limitato ai client del provider.
- `flightcore/engine.py` e `providers/`: motore precedente della pagina classic.

Sono librerie non ufficiali: modifiche a Google, consenso, limitazioni dell'IP
o cambi del formato possono interrompere il servizio. Un singolo processo
Flask gestisce un lavoro attivo per volta. Questa versione è pensata per uso
locale; non contiene una coda condivisa tra più processi o autenticazione per
un servizio pubblico.

Parametri e ciclo di vita sono documentati in [docs/API.md](docs/API.md).

## Verifica

```bash
python -m pytest -q
python -m ruff check .
node --check static/weekends.js
```

I test usano quotazioni sintetiche e cataloghi locali: non interrogano Google.
Coprono filtri al minuto, date, fusi e cambio ora, arrivi il giorno successivo,
somma dei prezzi, righe incomplete, cache, blocchi, arresto/ripresa,
persistenza e ricontrollo.

## Licenza e dipendenze

Codice del portale: MIT. Le dipendenze mantengono le rispettive licenze:
[punitarani/fli](https://github.com/punitarani/fli) e
[AWeirdDev/flights](https://github.com/AWeirdDev/flights).
