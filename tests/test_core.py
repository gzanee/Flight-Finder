"""
Test delle parti che non richiedono rete.

Le ricerche vere dipendono da Google e dal momento della giornata; qui si
verifica la logica che possiamo controllare: catalogo aeroporti, rotte e
validazione delle richieste.
"""

import pytest

from flightcore import airports

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
        ({"origin": "XXX", "destination": "BCN"}, "Partenza:"),
        ({"origin": "VCE", "destination": "ZZZ"}, "Destinazioni:"),
        (
            {"origin": "VCE", "destination": "BCN", "departure_date": "2020-01-01"},
            "Periodo non valido",
        ),
        ({"origin": "VCE", "destination": "BCN", "departure_date": "boh"}, "Indica inizio e fine"),
        (
            {"origin": "VCE", "destination": "BCN", "departure_time_min": "99:00"},
            "Orario non valido",
        ),
        ({"origin": "VCE", "destination": "BCN", "adults": 50}, "adults:"),
    ],
)
def test_la_validazione_rifiuta_gli_input_sbagliati(client, body, atteso):
    r = client.post("/api/search", json=body)
    assert r.status_code == 400
    assert atteso in r.get_json()["error"]


def test_ritorno_prima_dellandata_viene_rifiutato(client):
    r = client.post(
        "/api/search",
        json={
            "origin": "VCE",
            "destination": "BCN",
            "departure_date": "2026-12-10",
            "return_date": "2026-12-01",
        },
    )
    assert r.status_code == 400
    assert "Periodo non valido" in r.get_json()["error"]


def test_calendario_senza_destinazione_viene_rifiutato(client):
    r = client.post("/api/cheapest-dates", json={"origin": "VCE"})
    assert r.status_code == 400


def test_autocomplete_via_api(client):
    dati = client.get("/api/airports?q=venez").get_json()
    assert dati["success"] is True
    assert any(a["code"] == "VCE" for a in dati["airports"])


def test_health_elenca_le_librerie(client):
    dati = client.get("/api/health").get_json()
    assert set(dati["libraries"]) == {"fli", "fast-flights"}
    assert dati["currency"] == "EUR"



# ------------------------------------------------------------------ rotte --


def test_le_rotte_reali_sono_separate_per_aeroporto():
    """VCE e TSF non servono le stesse citta': unirle sprecherebbe richieste."""
    from flightcore import airports

    mappa = airports.resolve_destinations(["VCE", "TSF"], set_key="real")
    assert set(mappa) == {"VCE", "TSF"}
    assert mappa["VCE"] != mappa["TSF"]
    assert len(mappa["VCE"]) > 50
    assert all(airports.exists(c) for codes in mappa.values() for c in codes)


def test_lista_personalizzata_accetta_stringa_e_lista():
    from flightcore.flexible import airport_codes

    assert airport_codes("BCN, LIS ATH", "Destinazioni", 200) == ("BCN", "LIS", "ATH")
    assert airport_codes(["bcn", "lis"], "Destinazioni", 200) == ("BCN", "LIS")
    assert airport_codes([], "Destinazioni", 200) == ()


@pytest.mark.parametrize("codes", ["ZZZ, QQQ", "BCN, ZZZ"])
def test_lista_personalizzata_non_scartata_silenziosamente(codes):
    from flightcore.flexible import SearchValidationError, airport_codes

    with pytest.raises(SearchValidationError):
        airport_codes(codes, "Destinazioni", 200)


def test_nomi_italiani_e_aeroporti_secondari_nella_citta():
    from flightcore import airports
    from flightcore.italian_names import pick_city

    assert airports.details("GDN") == {
        "code": "GDN",
        "city": "Danzica",
        "name": "Aeroporto di Danzica-Lech Wałęsa",
        "country": "pl",
    }
    assert {airports.details(c)["city"] for c in ("STN", "LTN", "LGW", "LHR")} == {"Londra"}
    assert airports.details("BVA")["city"] == airports.details("CDG")["city"] == "Parigi"
    assert airports.details("CRL")["city"] == "Bruxelles"
    assert airports.details("VCE")["city"] == "Venezia"
    assert airports.details("TSF")["city"] == "Treviso"
    # Fuori dallo snapshot resta il catalogo offline.
    assert airports.details("XXQ")["name"] == "Aeroporto non identificato"
    assert pick_city("Aeroporto di Danzica-Lech Wałęsa", ["Tripla Città", "Danzica"]) == "Danzica"
    assert pick_city("Aeroporto di Palma di Maiorca", ["Maiorca", "Palma di Maiorca"]) == (
        "Palma di Maiorca"
    )
