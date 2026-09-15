"""
Test delle parti che non richiedono rete.

Le ricerche vere dipendono da Google e dal momento della giornata; qui si
verifica la logica che possiamo controllare: circuit breaker, normalizzazione
dei risultati, catalogo aeroporti e validazione delle richieste.
"""

from datetime import datetime, timedelta

import pytest

from flightcore import airports
from flightcore.breaker import CircuitBreaker
from flightcore.cache import TTLCache
from flightcore.models import Leg, Offer, Segment

# ---------------------------------------------------------------- breaker --


def _breaker():
    return CircuitBreaker(threshold=3, empty_threshold=8, cooldown=300)


def test_breaker_chiuso_allinizio():
    assert _breaker().is_open("fli") is False


def test_breaker_apre_dopo_tre_guasti():
    b = _breaker()
    for _ in range(3):
        b.record_failure("fli", "boom")
    assert b.is_open("fli") is True


def test_successo_azzera_i_guasti():
    b = _breaker()
    b.record_failure("fli")
    b.record_failure("fli")
    b.record_success("fli")
    b.record_failure("fli")
    assert b.is_open("fli") is False


def test_risposte_vuote_pesano_meno_dei_guasti():
    """Una rotta senza voli non deve mettere in pausa un provider che funziona."""
    b = _breaker()
    for _ in range(7):
        b.record_empty("fast-flights")
    assert b.is_open("fast-flights") is False
    b.record_empty("fast-flights")
    assert b.is_open("fast-flights") is True


def test_riapre_dopo_il_cooldown():
    b = CircuitBreaker(threshold=2, cooldown=0)
    b.record_failure("fli")
    b.record_failure("fli")
    assert b.is_open("fli") is False  # cooldown gia' scaduto: si riprova


def test_stato_riporta_la_pausa():
    b = _breaker()
    for _ in range(3):
        b.record_failure("fli", "rifiutato")
    stato = b.status()["fli"]
    assert stato["paused"] is True
    assert stato["last_error"] == "rifiutato"


# ------------------------------------------------------------------ cache --


def test_cache_restituisce_il_valore_memorizzato():
    c = TTLCache(ttl=60)
    c.set("k", [1, 2])
    assert c.get("k") == [1, 2]


def test_cache_scade():
    c = TTLCache(ttl=-1)
    c.set("k", "v")
    assert c.get("k") is None


def test_cache_non_supera_il_tetto():
    c = TTLCache(ttl=60, max_entries=3)
    for i in range(10):
        c.set(i, i)
    assert len(c._data) <= 3


# ----------------------------------------------------------------- modelli --


def _segment(frm, to, dep_h, arr_h, airline="Ryanair"):
    base = datetime(2026, 10, 23)
    return Segment(
        from_code=frm,
        to_code=to,
        departure=base + timedelta(hours=dep_h),
        arrival=base + timedelta(hours=arr_h),
        duration_min=(arr_h - dep_h) * 60,
        airline_name=airline,
    )


def test_leg_calcola_scali_e_durata_totale():
    """La durata deve includere il tempo a terra, non solo i minuti in volo."""
    leg = Leg(segments=[_segment("VCE", "BCN", 8, 10), _segment("BCN", "LHR", 14, 16)])
    assert leg.stops == 1
    assert leg.layover_codes == ["BCN"]
    assert leg.duration_min == 8 * 60  # 08:00 -> 16:00, scalo incluso


def test_offer_espone_scalo_peggiore_e_compagnie_senza_duplicati():
    andata = Leg(segments=[_segment("VCE", "BCN", 8, 10)])
    ritorno = Leg(segments=[_segment("BCN", "MAD", 18, 19), _segment("MAD", "VCE", 21, 23)])
    offer = Offer(price=99.0, currency="EUR", legs=[andata, ritorno],
                  origin="VCE", destination="BCN", provider="test")
    assert offer.stops == 1
    assert offer.airlines == ["Ryanair"]


def test_to_dict_e_serializzabile_e_segnala_il_ritorno_mancante():
    import json

    leg = Leg(segments=[_segment("VCE", "BCN", 8, 10)])
    offer = Offer(price=60.0, currency="EUR", legs=[leg], origin="VCE",
                  destination="BCN", provider="test", round_trip=True)
    data = offer.to_dict()
    assert data["inbound_pending"] is True
    assert data["outbound"]["from_code"] == "VCE"
    json.dumps(data)  # non deve sollevare


# --------------------------------------------------------------- aeroporti --


def test_autocomplete_trova_per_codice():
    assert airports.search("BCN")[0]["code"] == "BCN"


def test_autocomplete_capisce_i_nomi_italiani():
    codici = [a["code"] for a in airports.search("londra")]
    assert "LHR" in codici and "STN" in codici


def test_autocomplete_ignora_gli_accenti():
    assert any(a["code"] == "ZRH" for a in airports.search("zurich"))


def test_autocomplete_richiede_almeno_due_caratteri():
    assert airports.search("a") == []


def test_rose_di_destinazioni_contengono_solo_codici_validi():
    for key in airports.DESTINATION_SETS:
        codici = airports.destination_set(key)
        assert codici
        assert all(airports.exists(c) for c in codici)
        assert len(codici) == len(set(codici))


# ------------------------------------------------------------------- API ---


@pytest.fixture
def client():
    from app import app

    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.parametrize(
    "body, atteso",
    [
        ({"origin": "XXX", "destination": "BCN"}, "partenza sconosciuto"),
        ({"origin": "VCE", "destination": "ZZZ"}, "Destinazione sconosciuta"),
        ({"origin": "VCE", "destination": "BCN", "departure_date": "2020-01-01"}, "passato"),
        ({"origin": "VCE", "destination": "BCN", "departure_date": "boh"}, "Data non valida"),
        ({"origin": "VCE", "destination": "BCN", "departure_time_min": "99:00"},
         "fuori intervallo"),
        ({"origin": "VCE", "destination": "BCN", "adults": 50}, "troppo alto"),
    ],
)
def test_la_validazione_rifiuta_gli_input_sbagliati(client, body, atteso):
    r = client.post("/api/search", json=body)
    assert r.status_code == 400
    assert atteso in r.get_json()["error"]


def test_ritorno_prima_dellandata_viene_rifiutato(client):
    r = client.post("/api/search", json={
        "origin": "VCE", "destination": "BCN",
        "departure_date": "2026-12-10", "return_date": "2026-12-01"})
    assert r.status_code == 400
    assert "precedente" in r.get_json()["error"]


def test_calendario_senza_destinazione_viene_rifiutato(client):
    r = client.post("/api/cheapest-dates", json={"origin": "VCE"})
    assert r.status_code == 400


def test_autocomplete_via_api(client):
    dati = client.get("/api/airports?q=venez").get_json()
    assert dati["success"] is True
    assert any(a["code"] == "VCE" for a in dati["airports"])


def test_health_elenca_i_provider(client):
    dati = client.get("/api/health").get_json()
    assert "fli" in dati["providers"]
    assert dati["currency"] == "EUR"


# ------------------------------------------------------- filtri lato client --


def _offer(price, dep_h, stops=0, ritorno_h=None):
    """Offerta sintetica: andata alle dep_h, con eventuale ritorno alle ritorno_h."""
    segs = [_segment("VCE", "BCN", dep_h, dep_h + 2)]
    if stops:
        segs = [_segment("VCE", "MAD", dep_h, dep_h + 1),
                _segment("MAD", "BCN", dep_h + 3, dep_h + 4)]
    legs = [Leg(segments=segs)]
    if ritorno_h is not None:
        legs.append(Leg(segments=[_segment("BCN", "VCE", ritorno_h, ritorno_h + 2)]))
    return Offer(price=price, currency="EUR", legs=legs, origin="VCE",
                 destination="BCN", provider="test")


def _filtra(offerte, **kwargs):
    from flightcore.engine import _apply_client_filters
    from flightcore.models import SearchQuery

    query = SearchQuery(origin="VCE", destination="BCN",
                        departure_date="2026-10-23", **kwargs)
    return _apply_client_filters(offerte, query)


def test_filtro_prezzo_massimo():
    tenute = _filtra([_offer(50, 8), _offer(150, 9)], max_price=100)
    assert [o.price for o in tenute] == [50]


def test_filtro_numero_di_scali():
    tenute = _filtra([_offer(50, 8, stops=1), _offer(80, 9, stops=0)], max_stops=0)
    assert [o.price for o in tenute] == [80]


def test_filtro_ora_minima_di_partenza():
    tenute = _filtra([_offer(50, 6), _offer(80, 19)], earliest_departure=18)
    assert [o.price for o in tenute] == [80]


def test_filtro_ora_massima_di_partenza():
    tenute = _filtra([_offer(50, 6), _offer(80, 22)], latest_departure=20)
    assert [o.price for o in tenute] == [50]


def test_filtro_orario_del_ritorno():
    offerte = [_offer(50, 8, ritorno_h=9), _offer(80, 8, ritorno_h=18)]
    tenute = _filtra(offerte, return_earliest_departure=15)
    assert [o.price for o in tenute] == [80]


def test_il_filtro_sul_ritorno_non_tocca_il_solo_andata():
    """Un'offerta senza ritorno non deve sparire per un vincolo sul ritorno."""
    tenute = _filtra([_offer(50, 8)], return_earliest_departure=15)
    assert len(tenute) == 1


def test_ordinamento_predefinito_per_prezzo():
    tenute = _filtra([_offer(120, 8), _offer(40, 9), _offer(80, 10)])
    assert [o.price for o in tenute] == [40, 80, 120]


def test_ordinamento_per_orario_di_partenza():
    tenute = _filtra([_offer(120, 15), _offer(40, 6)], sort_by="departure")
    assert [o.legs[0].departure.hour for o in tenute] == [6, 15]


# ------------------------------------------------- scansioni e limitazioni --


def test_le_ricerche_si_alternano_fra_le_origini():
    """
    Su una scansione lunga Google strozza a meta' strada: se si esaurisse un
    aeroporto alla volta, l'ultimo perderebbe quasi tutti i risultati.
    """
    from flightcore.engine import _interleave

    mescolate = _interleave([["a1", "a2", "a3", "a4"], ["b1", "b2"]])
    assert mescolate == ["a1", "b1", "a2", "b2", "a3", "a4"]


def test_interleave_con_un_solo_gruppo():
    from flightcore.engine import _interleave

    assert _interleave([["x", "y"]]) == ["x", "y"]


def test_il_limitatore_rallenta_le_raffiche():
    import time

    from flightcore.ratelimit import RateLimiter

    limiter = RateLimiter(rate=20.0, burst=2)
    inizio = time.monotonic()
    for _ in range(5):
        limiter.acquire()
    trascorso = time.monotonic() - inizio
    # 2 immediate grazie alla raffica, le altre 3 a 20/s => almeno 0,15 s
    assert trascorso >= 0.1


def test_riconosce_la_pagina_di_limitazione():
    from flightcore.providers.fastflights_provider import _rileva_blocco

    messaggio = _rileva_blocco("<html><body>Our systems have detected unusual traffic</body>")
    assert messaggio is not None
    assert "limitato le richieste" in messaggio


def test_riconosce_la_pagina_di_consenso():
    from flightcore.providers.fastflights_provider import _rileva_blocco

    messaggio = _rileva_blocco('<html><base href="https://consent.google.com/">')
    assert messaggio is not None
    assert "SOCS" in messaggio


def test_una_pagina_normale_non_viene_scambiata_per_un_blocco():
    from flightcore.providers.fastflights_provider import _rileva_blocco

    assert _rileva_blocco("<html><body>AF_initDataCallback Ryanair Vueling</body>") is None


def test_la_diagnosi_spiega_la_limitazione_invece_di_tacere():
    from flightcore.engine import Engine
    from flightcore.providers.base import Provider

    class Muto(Provider):
        def __init__(self, nome):
            self.name = nome

        def search(self, query):
            return []

    motore = Engine(providers=[Muto("uno"), Muto("due")])
    assert motore.diagnosis() is None  # finche' nulla e' in pausa, nessun allarme

    for nome in ("uno", "due"):
        for _ in range(3):
            motore.breaker.record_failure(nome, "Google ha temporaneamente limitato le richieste")
    diagnosi = motore.diagnosis()
    assert diagnosi is not None
    assert "limitato le richieste" in diagnosi


def test_nessuna_diagnosi_se_almeno_un_provider_e_vivo():
    from flightcore.engine import Engine
    from flightcore.providers.base import Provider

    class Muto(Provider):
        def __init__(self, nome):
            self.name = nome

        def search(self, query):
            return []

    motore = Engine(providers=[Muto("uno"), Muto("due")])
    for _ in range(3):
        motore.breaker.record_failure("uno", "rotto")
    assert motore.diagnosis() is None


def test_le_rotte_reali_sono_separate_per_aeroporto():
    """VCE e TSF non servono le stesse citta': unirle sprecherebbe richieste."""
    from flightcore import airports

    mappa = airports.resolve_destinations(["VCE", "TSF"], set_key="real")
    assert set(mappa) == {"VCE", "TSF"}
    assert mappa["VCE"] != mappa["TSF"]
    assert len(mappa["VCE"]) > 50
    assert all(airports.exists(c) for codes in mappa.values() for c in codes)


def test_lista_personalizzata_accetta_stringa_e_lista():
    from app import _destination_list

    assert _destination_list("BCN, LIS ATH") == ["BCN", "LIS", "ATH"]
    assert _destination_list(["bcn", "lis"]) == ["BCN", "LIS"]
    assert _destination_list(None) is None


def test_lista_personalizzata_rifiuta_codici_inesistenti():
    import pytest as _pytest

    from app import BadRequest, _destination_list

    with _pytest.raises(BadRequest):
        _destination_list("ZZZ, QQQ")


def test_lista_personalizzata_tiene_i_codici_validi_ignorando_gli_altri():
    from app import _destination_list

    assert _destination_list("BCN, ZZZ") == ["BCN"]
