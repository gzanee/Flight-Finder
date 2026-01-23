# ✈️ Flight Finder - Voli economici da VCE/TSF

Un'applicazione web per trovare voli economici da Venezia Marco Polo (VCE) e Treviso (TSF) verso qualsiasi destinazione.

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-3.0-green.svg)
![Amadeus](https://img.shields.io/badge/Amadeus-API-orange.svg)

## 🎯 Funzionalità

- **Ricerca "Ovunque"**: Trova destinazioni economiche senza specificare dove vuoi andare
- **Filtri avanzati**:
  - Solo voli diretti
  - Prezzo massimo
  - Orario minimo partenza (es. "dalle 18:00 in poi")
  - Orario minimo ritorno
- **Ricerca da entrambi gli aeroporti** (VCE + TSF) in un'unica query
- **Modalità "Ispirami"**: Scopri le destinazioni più economiche del periodo
- **Ordinamento risultati** per prezzo o orario

## 🚀 Quick Start

### 1. Prerequisiti

- Python 3.9 o superiore
- Account Amadeus for Developers (gratuito)

### 2. Ottieni le credenziali Amadeus

1. Vai su [developers.amadeus.com](https://developers.amadeus.com)
2. Registrati gratuitamente
3. Crea una nuova app nel tuo dashboard
4. Copia **API Key** e **API Secret**

### 3. Installazione

```bash
# Clona o scarica il progetto
cd flight-finder

# Crea un ambiente virtuale (consigliato)
python -m venv venv
source venv/bin/activate  # Linux/Mac
# oppure: venv\Scripts\activate  # Windows

# Installa le dipendenze
pip install -r requirements.txt

# Configura le credenziali
cp .env.example .env
# Modifica .env e inserisci le tue credenziali Amadeus
```

### 4. Avvia l'applicazione

```bash
python app.py
```

Apri il browser su [http://localhost:5000](http://localhost:5000)

## 📖 Uso

### Ricerca base
1. Seleziona l'aeroporto di partenza (VCE, TSF o entrambi)
2. Lascia la destinazione vuota per cercare "ovunque"
3. Imposta le date di andata e ritorno
4. Clicca **Cerca Voli**

### Esempio: Weekend economico
Per trovare un volo weekend (venerdì sera → domenica pomeriggio) sotto i 100€:

1. **Partenza da**: VCE o TSF (o entrambi)
2. **Destinazione**: lascia vuoto
3. **Data Andata**: venerdì della settimana desiderata
4. **Partenza andata dalle ore**: 18:00
5. **Data Ritorno**: domenica
6. **Partenza ritorno dalle ore**: 15:00
7. **Prezzo massimo**: 100
8. **Solo voli diretti**: ✓ attivo

### Modalità "Ispirami"
Clicca il pulsante **Ispirami** per vedere una lista di destinazioni economiche ordinate per prezzo. Clicca su una destinazione per vedere i voli disponibili.

## 🛠️ Struttura del progetto

```
flight-finder/
├── app.py              # Backend Flask e logica API
├── requirements.txt    # Dipendenze Python
├── .env.example        # Template variabili d'ambiente
├── .env                # Le TUE credenziali (da creare)
├── templates/
│   └── index.html      # Frontend UI
└── README.md           # Questo file
```

## ⚙️ API Endpoints

| Endpoint | Metodo | Descrizione |
|----------|--------|-------------|
| `/` | GET | Pagina principale |
| `/api/search/flights` | POST | Ricerca voli con filtri |
| `/api/search/inspiration` | POST | Trova destinazioni economiche |
| `/api/cheapest-dates` | POST | Date più economiche per una rotta |
| `/api/airports/search` | GET | Autocomplete aeroporti |

## 🔧 Configurazione avanzata

### Ambiente di produzione
Di default l'app usa l'ambiente **test** di Amadeus (dati simulati ma realistici). Per passare a dati **reali**:

1. In `app.py`, cambia:
   ```python
   hostname='test'  →  hostname='production'
   ```
2. Richiedi l'approvazione produzione su Amadeus Developers

### Limiti API (Free Tier)
- ~2000 chiamate/mese in test
- Sufficienti per uso personale

## 🔮 Sviluppi futuri

- [ ] Notifiche email/Telegram su calo prezzi
- [ ] Storico prezzi e grafici
- [ ] Integrazione Travelpayouts per più fonti dati
- [ ] Salvataggio ricerche preferite
- [ ] Deploy su Heroku/Railway

## 📝 Note

- I prezzi sono **indicativi** e soggetti a disponibilità
- Le low-cost (Ryanair, easyJet, Wizz) sono incluse dal 2022
- Il prezzo mostrato è per **tutti i passeggeri** selezionati
- Non include eventuali bagagli extra

## 📄 Licenza

MIT License - Usa liberamente per progetti personali.

---

Creato con ❤️ per trovare voli economici dal Veneto
