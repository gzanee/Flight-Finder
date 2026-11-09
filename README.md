# Flight Finder

Un solo motore per cercare voli con **date fisse o flessibili**, **sola andata
oppure andata e ritorno**, da più aeroporti e verso destinazioni specifiche o
**Ovunque**. La ricerca usa quotazioni Google Flights, conserva l’avanzamento
e permette di ricontrollare gli stessi voli prima di aprire il venditore.

## Il modulo unico

- Aeroporti cercabili per nome, città o codice IATA; fino a 10 origini e 200
  destinazioni. Origini precompilate: Venezia e Treviso, modificabili.
- Destinazioni candidate complete oppure gruppi come Europa, mare, city break
  e lungo raggio, recuperati dalla ricerca precedente.
- Date fisse: una partenza e, per l’A/R, un ritorno esatti. Giorni della
  settimana e notti non escludono le date selezionate.
- Date flessibili: periodo, giorni di partenza/ritorno e soggiorno da 0 a
  30 notti. Zero notti permette un ritorno in giornata.
- Il preset **Weekend** compila venerdì dopo le 18, domenica dopo le 18,
  2 notti: è una scorciatoia dello stesso modulo.
- Ritorno nello stesso aeroporto oppure in uno del gruppo di origine;
  ripartenza dallo stesso aeroporto di arrivo oppure da uno del gruppo di
  destinazione esplicito. Gli spostamenti durante il soggiorno non sono inclusi.
- Partenze e arrivi al minuto; arrivo finale entro il giorno di viaggio;
  durata minima/massima delle tratte, inclusi gli scali.
- Diretti o fino a tre scali, durata minima/massima della coincidenza,
  self-transfer, cambio di aeroporto durante uno scalo e scali solo in area
  Schengen come preferenze separate.
- Adulti, bambini con posto, neonati in braccio; classe, filtro bagagli,
  compagnie specifiche, selezione low-cost ed esclusione Basic economy.
- Ordinamento per prezzo totale, tempo a destinazione, durata dei voli o
  partenza. Riepilogo dei prezzi minimi per data sugli stessi risultati live.

La pagina è `/`. I vecchi indirizzi `/classic` e `/weekends` rimandano lì:
non ci sono più pagine o worker separati.

## Esplora i risultati

- **Per destinazione**: una scheda per aeroporto di arrivo con prezzo minimo,
  numero di combinazioni e anteprima del viaggio migliore per l'ordinamento scelto.
  Aprila per vedere tutti i suoi voli oppure passa a **Tutti i voli**.
- **Affina i risultati**: prezzo massimo totale, tempo minimo a destinazione,
  origine, destinazione, compagnia su ogni segmento, rientro entro un orario
  nello stesso giorno e destinazioni da escludere. Un pulsante azzera i filtri.
- **Tre modi di scegliere**: viaggio più economico, più tempo a destinazione
  e miglior compromesso. Quest'ultimo mostra euro aggiuntivi e ore guadagnate:
  ammette al massimo il maggiore tra 20 € e il 50% del prezzo minimo, e
  3 € per ogni ora guadagnata. Tra i candidati sceglie il costo per ora più
  basso. Se nessuno conviene, lo dichiara. In sola andata propone solo il più
  economico; i viaggi con cambio di aeroporto a destinazione non partecipano
  al confronto del tempo.

Filtri, riepiloghi, calendario e suggerimenti lavorano su **tutti** i risultati
salvati, prima della paginazione, e non interrogano Google. Riflettono anche i
ricontrolli del prezzo e le rimozioni dei voli non confermati. I suggerimenti
sono relativi al bacino filtrato e riportano quando il prezzo va aggiornato.
Con una ricerca incompleta sono esplicitamente parziali.

## Avvio

Verificato con Python 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Apri [il portale locale](http://localhost:5001/). La porta si può cambiare
con `PORT`. In sviluppo è disponibile anche `flask --app app run`.

## Dati e verifica

Per l’A/R confrontiamo due quotazioni di sola andata e ne sommiamo i prezzi,
per tutti i viaggiatori. Si tratta di **due biglietti separati**, non di una
tariffa A/R unica. In sola andata non vengono eseguite ricerche di ritorno.

`verification: google_live` indica dati di shopping osservati su Google:
numero volo, aeroporti, data, orari e prezzo. Non è una conferma della
compagnia: `airline_verified` resta `false`. I prezzi più vecchi di cinque
minuti sono marcati da ricontrollare. Un formato sconosciuto, un blocco o
un errore di connessione non viene interpretato come zero voli.

Filtri su orari, scali, durata e compagnie vengono verificati nuovamente sui
voli restituiti. Classe, bagagli ed esclusione Basic economy sono filtri
inviati a Google; condizioni e inclusioni esatte vanno confermate dal venditore.
Il portale non applica sconti per abbonamenti personali come Wizz Discount
Club o Megavolotea, e non prenota biglietti.

Il cambio di aeroporto durante il soggiorno è distinto da un cambio durante
una coincidenza. Con aeroporti di destinazione diversi mostriamo il tempo
fra i due voli: non stimiamo il tempo né il costo del trasferimento a terra.
Il filtro Schengen usa i paesi della [lista del Consiglio UE](https://www.consilium.europa.eu/en/policies/schengen-area/)
e il paese dell’aeroporto; non è una verifica dei requisiti d’ingresso personali.

## Copertura di Ovunque e tempi

Wikipedia/Wikidata forniscono destinazioni candidate, non disponibilità o
calendari operativi. Per origini senza catalogo locale usiamo il bacino
complessivo dei cataloghi disponibili. La copertura è **non esaustiva**, anche
per rotte nuove o con scalo. Le destinazioni esplicite permettono di cercare
fuori dal bacino. Non scartiamo una rotta solo perché assente da una tabella.

Prima dell’avvio mostriamo il numero di combinazioni e un massimo di richieste.
Una scansione su molti mesi e aeroporti può richiedere ore. Tutte le modalità
usano lo stesso worker seriale, la stessa cache di cinque minuti e lo stesso
intervallo: **0,3 secondi dopo ogni risposta**, configurabile fra 0,3 e 10 con
`FLIGHT_FINDER_GOOGLE_INTERVAL`. La connessione con Google viene riusata fra
una ricerca e l'altra. Si aggiungono i tempi di risposta e gli
errori di rete: non è una garanzia di durata o di assenza di blocchi.

Un errore di connessione viene ritentato al massimo due volte. Un 403/429
ferma la ricerca e impone una pausa di quindici minuti nel processo.
Il progresso vive in `.cache/weekends.sqlite3` e può essere ripreso dopo
un riavvio; i lavori salvati dalla precedente versione restano leggibili.
`FLIGHT_FINDER_CACHE_DIR` permette di scegliere la directory. Il browser
ripristina l’ultima ricerca, e il calendario riassume i risultati verificati
finora senza una seconda scansione.

## Architettura e API

- `flightcore/flexible.py`: richiesta unica, date, pianificazione e filtri.
- `flightcore/result_browser.py`: filtri sui risultati salvati, gruppi e suggerimenti.
- `flightcore/live_quotes.py`: query fast-flights, decoder fli, cache e retry.
- `flightcore/flexible_jobs.py`: un solo worker, persistenza e ricontrollo.
- `flightcore/flexible_api.py`: pagina e API del motore unico.
- `static/weekends.*`, `templates/weekends.html`: interfaccia unica.
- `app.py`: avvio, autocomplete e traduzione delle vecchie richieste.

I nomi di alcuni file conservano “flexible” o “weekends” per continuità;
tutte le modalità usano questi stessi componenti.

Le API principali sono `/api/search/plan` e `/api/search/jobs`; `/api/flexible/*`
resta un alias sullo stesso manager. Le vecchie `/api/search`, `/api/explore`
e `/api/cheapest-dates` traducono i parametri e restituiscono ora **202 con
l’identificativo del lavoro**, non un elenco sincrono di offerte.
Vedi [docs/API.md](docs/API.md).

## Verifica

```bash
python -m pytest -q
python -m ruff check .
node --check static/weekends.js
```

I test usano risposte sintetiche senza interrogare Google. Coprono le quattro
combinazioni tipo viaggio/date, filtri, andata e ritorno verificati,
coincidenze, gruppi di aeroporti, cache, arresto/ripresa, migrazione dei vecchi
lavori e ricontrollo. È un servizio locale a processo singolo, senza coda
condivisa o autenticazione per una pubblicazione multiutente.

## Licenza

MIT. Le dipendenze mantengono le loro licenze:
[punitarani/fli](https://github.com/punitarani/fli) e
[AWeirdDev/flights](https://github.com/AWeirdDev/flights).
