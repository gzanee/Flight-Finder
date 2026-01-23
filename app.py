"""
Flight Finder - Trova voli economici da VCE/TSF
Utilizza le API Amadeus Self-Service
"""

import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from amadeus import Client, ResponseError
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Configurazione Amadeus
amadeus = Client(
    client_id=os.getenv('AMADEUS_CLIENT_ID'),
    client_secret=os.getenv('AMADEUS_CLIENT_SECRET'),
    hostname='test'  # Cambia in 'production' per dati reali
)

# Aeroporti di partenza supportati
AIRPORTS = {
    'VCE': 'Venezia Marco Polo',
    'TSF': 'Treviso Antonio Canova'
}


@app.route('/')
def index():
    """Pagina principale con form di ricerca"""
    return render_template('index.html', airports=AIRPORTS)


@app.route('/api/search/inspiration', methods=['POST'])
def search_inspiration():
    """
    Cerca destinazioni economiche da un aeroporto
    Usa Flight Inspiration Search per trovare "ovunque" economico
    """
    data = request.json
    origin = data.get('origin', 'VCE')
    departure_date = data.get('departure_date')
    max_price = data.get('max_price', 100)
    
    try:
        # Flight Inspiration Search - trova destinazioni economiche
        response = amadeus.shopping.flight_destinations.get(
            origin=origin,
            departureDate=departure_date,
            maxPrice=max_price,
            nonStop='true' if data.get('direct_only', True) else 'false'
        )
        
        destinations = []
        for dest in response.data:
            destinations.append({
                'destination': dest.get('destination'),
                'departure_date': dest.get('departureDate'),
                'return_date': dest.get('returnDate'),
                'price': dest.get('price', {}).get('total'),
                'currency': dest.get('price', {}).get('currency', 'EUR')
            })
        
        return jsonify({
            'success': True,
            'count': len(destinations),
            'destinations': destinations
        })
        
    except ResponseError as error:
        return jsonify({
            'success': False,
            'error': str(error)
        }), 400


@app.route('/api/search/flights', methods=['POST'])
def search_flights():
    """
    Cerca voli specifici con tutti i filtri
    Usa Flight Offers Search per ricerca dettagliata
    """
    data = request.json
    
    origin = data.get('origin', 'VCE')
    destination = data.get('destination')  # Può essere None per "ovunque"
    departure_date = data.get('departure_date')
    return_date = data.get('return_date')
    max_price = int(data.get('max_price', 100))
    direct_only = data.get('direct_only', True)
    departure_time_min = data.get('departure_time_min', '00:00')
    return_time_min = data.get('return_time_min', '00:00')
    adults = data.get('adults', 1)
    
    try:
        # Se destinazione non specificata, prima trova destinazioni economiche
        if not destination or destination == 'anywhere':
            return search_anywhere(
                origin, departure_date, return_date, max_price, 
                direct_only, departure_time_min, return_time_min, adults
            )
        
        # Ricerca voli specifici
        search_params = {
            'originLocationCode': origin,
            'destinationLocationCode': destination,
            'departureDate': departure_date,
            'adults': adults,
            'currencyCode': 'EUR',
            'max': 50
        }
        
        if return_date:
            search_params['returnDate'] = return_date
        
        if direct_only:
            search_params['nonStop'] = 'true'
        
        response = amadeus.shopping.flight_offers_search.get(**search_params)
        
        # Filtra e formatta i risultati
        flights = filter_and_format_flights(
            response.data, 
            max_price, 
            departure_time_min, 
            return_time_min
        )
        
        return jsonify({
            'success': True,
            'count': len(flights),
            'flights': flights
        })
        
    except ResponseError as error:
        return jsonify({
            'success': False,
            'error': str(error)
        }), 400


def search_anywhere(origin, departure_date, return_date, max_price, 
                    direct_only, departure_time_min, return_time_min, adults):
    """
    Cerca voli verso "ovunque" - prima trova destinazioni, poi cerca voli
    """
    all_flights = []
    errors = []
    
    try:
        # Step 1: Trova destinazioni economiche
        inspiration_response = amadeus.shopping.flight_destinations.get(
            origin=origin,
            departureDate=departure_date,
            maxPrice=max_price,
            nonStop='true' if direct_only else 'false'
        )
        
        destinations = [d.get('destination') for d in inspiration_response.data[:10]]  # Top 10
        
        # Step 2: Per ogni destinazione, cerca voli dettagliati
        for dest in destinations:
            try:
                search_params = {
                    'originLocationCode': origin,
                    'destinationLocationCode': dest,
                    'departureDate': departure_date,
                    'adults': adults,
                    'currencyCode': 'EUR',
                    'max': 10
                }
                
                if return_date:
                    search_params['returnDate'] = return_date
                
                if direct_only:
                    search_params['nonStop'] = 'true'
                
                response = amadeus.shopping.flight_offers_search.get(**search_params)
                
                flights = filter_and_format_flights(
                    response.data,
                    max_price,
                    departure_time_min,
                    return_time_min
                )
                
                all_flights.extend(flights)
                
            except ResponseError as e:
                errors.append(f"{dest}: {str(e)}")
                continue
        
        # Ordina per prezzo
        all_flights.sort(key=lambda x: float(x.get('total_price', 9999)))
        
        return jsonify({
            'success': True,
            'count': len(all_flights),
            'flights': all_flights,
            'searched_destinations': destinations,
            'errors': errors if errors else None
        })
        
    except ResponseError as error:
        return jsonify({
            'success': False,
            'error': str(error)
        }), 400


def filter_and_format_flights(flight_offers, max_price, departure_time_min, return_time_min):
    """
    Filtra i voli per prezzo e orario, e formatta i risultati
    """
    filtered_flights = []
    
    for offer in flight_offers:
        price = float(offer.get('price', {}).get('total', 9999))
        
        # Filtro prezzo
        if price > max_price:
            continue
        
        itineraries = offer.get('itineraries', [])
        if not itineraries:
            continue
        
        # Controlla orario partenza andata
        outbound = itineraries[0]
        outbound_segments = outbound.get('segments', [])
        if outbound_segments:
            departure_time = outbound_segments[0].get('departure', {}).get('at', '')
            if departure_time:
                dep_time = datetime.fromisoformat(departure_time.replace('Z', '+00:00'))
                min_time = datetime.strptime(departure_time_min, '%H:%M').time()
                if dep_time.time() < min_time:
                    continue
        
        # Controlla orario partenza ritorno (se presente)
        if len(itineraries) > 1 and return_time_min:
            inbound = itineraries[1]
            inbound_segments = inbound.get('segments', [])
            if inbound_segments:
                return_time = inbound_segments[0].get('departure', {}).get('at', '')
                if return_time:
                    ret_time = datetime.fromisoformat(return_time.replace('Z', '+00:00'))
                    min_ret_time = datetime.strptime(return_time_min, '%H:%M').time()
                    if ret_time.time() < min_ret_time:
                        continue
        
        # Formatta il volo
        formatted = format_flight_offer(offer)
        if formatted:
            filtered_flights.append(formatted)
    
    return filtered_flights


def format_flight_offer(offer):
    """
    Formatta un'offerta volo in un formato leggibile
    """
    try:
        itineraries = offer.get('itineraries', [])
        price_info = offer.get('price', {})
        
        result = {
            'id': offer.get('id'),
            'total_price': price_info.get('total'),
            'currency': price_info.get('currency', 'EUR'),
            'outbound': None,
            'inbound': None,
            'airlines': set(),
            'booking_class': offer.get('travelerPricings', [{}])[0].get('fareDetailsBySegment', [{}])[0].get('cabin', 'ECONOMY')
        }
        
        # Andata
        if itineraries:
            outbound = itineraries[0]
            segments = outbound.get('segments', [])
            if segments:
                first_seg = segments[0]
                last_seg = segments[-1]
                
                result['outbound'] = {
                    'departure_airport': first_seg.get('departure', {}).get('iataCode'),
                    'departure_time': first_seg.get('departure', {}).get('at'),
                    'arrival_airport': last_seg.get('arrival', {}).get('iataCode'),
                    'arrival_time': last_seg.get('arrival', {}).get('at'),
                    'duration': outbound.get('duration'),
                    'stops': len(segments) - 1,
                    'carriers': [s.get('carrierCode') for s in segments]
                }
                
                for seg in segments:
                    result['airlines'].add(seg.get('carrierCode'))
        
        # Ritorno
        if len(itineraries) > 1:
            inbound = itineraries[1]
            segments = inbound.get('segments', [])
            if segments:
                first_seg = segments[0]
                last_seg = segments[-1]
                
                result['inbound'] = {
                    'departure_airport': first_seg.get('departure', {}).get('iataCode'),
                    'departure_time': first_seg.get('departure', {}).get('at'),
                    'arrival_airport': last_seg.get('arrival', {}).get('iataCode'),
                    'arrival_time': last_seg.get('arrival', {}).get('at'),
                    'duration': inbound.get('duration'),
                    'stops': len(segments) - 1,
                    'carriers': [s.get('carrierCode') for s in segments]
                }
                
                for seg in segments:
                    result['airlines'].add(seg.get('carrierCode'))
        
        result['airlines'] = list(result['airlines'])
        return result
        
    except Exception as e:
        print(f"Errore formattazione volo: {e}")
        return None


@app.route('/api/cheapest-dates', methods=['POST'])
def cheapest_dates():
    """
    Trova le date più economiche per una rotta
    Usa Flight Cheapest Date Search
    """
    data = request.json
    origin = data.get('origin', 'VCE')
    destination = data.get('destination')
    
    if not destination:
        return jsonify({
            'success': False,
            'error': 'Destinazione richiesta per questa ricerca'
        }), 400
    
    try:
        response = amadeus.shopping.flight_dates.get(
            origin=origin,
            destination=destination
        )
        
        dates = []
        for item in response.data:
            dates.append({
                'departure_date': item.get('departureDate'),
                'return_date': item.get('returnDate'),
                'price': item.get('price', {}).get('total'),
                'currency': item.get('price', {}).get('currency', 'EUR')
            })
        
        return jsonify({
            'success': True,
            'count': len(dates),
            'dates': dates
        })
        
    except ResponseError as error:
        return jsonify({
            'success': False,
            'error': str(error)
        }), 400


@app.route('/api/airports/search', methods=['GET'])
def search_airports():
    """
    Cerca aeroporti per nome o codice
    """
    keyword = request.args.get('keyword', '')
    
    if len(keyword) < 2:
        return jsonify({
            'success': True,
            'airports': []
        })
    
    try:
        response = amadeus.reference_data.locations.get(
            keyword=keyword,
            subType='AIRPORT'
        )
        
        airports = []
        for loc in response.data[:10]:
            airports.append({
                'code': loc.get('iataCode'),
                'name': loc.get('name'),
                'city': loc.get('address', {}).get('cityName'),
                'country': loc.get('address', {}).get('countryName')
            })
        
        return jsonify({
            'success': True,
            'airports': airports
        })
        
    except ResponseError as error:
        return jsonify({
            'success': False,
            'error': str(error)
        }), 400


if __name__ == '__main__':
    # Verifica configurazione
    if not os.getenv('AMADEUS_CLIENT_ID') or not os.getenv('AMADEUS_CLIENT_SECRET'):
        print("=" * 60)
        print("ATTENZIONE: Configura le credenziali Amadeus!")
        print("1. Registrati su https://developers.amadeus.com")
        print("2. Crea un'app e ottieni Client ID e Secret")
        print("3. Copia .env.example in .env e inserisci le credenziali")
        print("=" * 60)
    
    app.run(debug=True, port=5000)
