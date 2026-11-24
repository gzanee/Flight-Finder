"""PDF dei risultati salvati, da mandare agli amici: una pagina di sintesi e i
dettagli per citta'. Nessuna chiamata a Google, solo i viaggi gia' trovati."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fpdf import FPDF

from .airports import details as airport_details

FONTS = Path(__file__).with_name("data") / "fonts"
FLAGS = Path(__file__).parents[1] / "static" / "flags"
LOCAL = ZoneInfo("Europe/Rome")
DAYS = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
MONTHS = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
PER_CITY = 3  # default; the export asks the user
MAX_PER_CITY = 1000  # only a sanity bound: a city has one line per weekend

INK = (20, 35, 58)
MUTED = (91, 107, 130)
ACCENT = (35, 87, 209)
LINE = (214, 222, 233)
HIGHLIGHT = (255, 236, 170)
BAND = (238, 242, 247)

MARGIN = 14
WIDTH = 210 - 2 * MARGIN
# Price | outbound | return | time at destination
COLUMNS = (22, 66, 66, WIDTH - 154)
ROW = 10.5
DASH = "\u2013"


def day(iso):
    d = datetime.fromisoformat(iso)
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def clock(iso):
    return datetime.fromisoformat(iso).strftime("%H:%M")


def euro(value):
    return f"{value:.0f} €" if value == int(value) else f"{value:.2f} €".replace(".", ",")


def hours(minutes):
    return f"{round(minutes / 60)} ore" if minutes else "—"


def city(code):
    return airport_details(code)["city"]


def destination(trip):
    return trip["outbound"]["segments"][-1]["to_code"]


def weekend(trip):
    back = trip["inbound"]
    return (
        trip["outbound"]["segments"][0]["departure"][:10],
        back["segments"][0]["departure"][:10] if back else None,
    )


def by_city(trips, per_city=PER_CITY):
    """Cities cheapest first; per city the per_city cheapest flights.

    Different weekends come first, so a short list still shows variety; the
    remaining places go to the next cheapest flights of any weekend.
    """
    grouped = {}
    for trip in trips:  # already sorted by price
        grouped.setdefault(city(destination(trip)), []).append(trip)
    output = []
    for name, items in grouped.items():
        picked, seen = [], set()
        for trip in items:
            if weekend(trip) not in seen:
                seen.add(weekend(trip))
                picked.append(trip)
            if len(picked) == per_city:
                break
        if len(picked) < per_city:
            chosen = {id(trip) for trip in picked}
            rest = [trip for trip in items if id(trip) not in chosen]
            picked += rest[: per_city - len(picked)]
            picked.sort(key=lambda trip: trip["price"])
        output.append(
            {
                "name": name,
                "country": airport_details(destination(items[0]))["country"],
                "codes": sorted({destination(t) for t in items}),
                "count": len(items),
                "trips": picked,
            }
        )
    return output


def leg_lines(leg):
    first, last = leg["segments"][0], leg["segments"][-1]
    next_day = last["arrival"][:10] != first["departure"][:10]
    times = (
        f"{day(first['departure'])}  {clock(first['departure'])} {first['from_code']}"
        f" → {clock(last['arrival'])}{' (+1)' if next_day else ''} {last['to_code']}"
    )
    airlines = []
    for segment in leg["segments"]:
        name = segment.get("airline_name") or segment["airline_code"]
        if name not in airlines:
            airlines.append(name)
    stops = leg["segments"][:-1]
    route = "diretto"
    if stops:
        via = ", ".join(segment["to_code"] for segment in stops)
        route = f"{len(stops)} scal{'o' if len(stops) == 1 else 'i'} ({via})"
    return times, f"{' + '.join(airlines)} · {route}"


def dates(trip):
    out, back = weekend(trip)
    return day(out) + (f" {DASH} {day(back)}" if back else "")


def subtitle(request):
    origins = " · ".join(city(code) for code in request["origins"])
    start = datetime.fromisoformat(request["start_date"])
    end = datetime.fromisoformat(request["end_date"])
    period = f"{day(request['start_date'])} {DASH} {day(request['end_date'])} {end.year}"
    if start == end:
        period = f"{day(request['start_date'])} {start.year}"
    kind = "andata e ritorno" if request["trip_type"] != "one_way" else "solo andata"
    travelers = request["adults"] + request["children"] + request["infants"]
    per = "a persona" if travelers == 1 else f"per {travelers} viaggiatori"
    return f"Da {origins} · {period} · {kind}, prezzi {per}"


def checked_label(trips):
    stamps = [datetime.fromisoformat(t["checked_at"]).astimezone(LOCAL) for t in trips]
    first, last = min(stamps), max(stamps)
    when = f"{first.day} {MONTHS[first.month - 1]} {first.year}"
    span = f"alle {first:%H:%M}" if first == last else f"tra le {first:%H:%M} e le {last:%H:%M}"
    return f"Prezzi visti su Google Flights il {when} {span}"


class Document(FPDF):
    def __init__(self):
        super().__init__(format="A4")
        self.add_font("Plex", "", FONTS / "IBMPlexSans-Regular.ttf")
        self.add_font("Plex", "B", FONTS / "IBMPlexSans-Bold.ttf")
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(auto=True, margin=16)

    def footer(self):
        self.text_style(8, "", MUTED)
        self.set_y(-11)
        self.cell(0, 5, f"Weekend Finder · pagina {self.page_no()} di {{nb}}", align="C")

    def text_style(self, size, style="", color=INK):
        self.set_font("Plex", style, size)
        self.set_text_color(*color)

    def flag(self, country, x, y, height):
        """Draw the country flag at x, y; return the horizontal space it takes."""
        path = FLAGS / f"{country}.png"
        if not country or not path.is_file():
            return 0
        width = height * 4 / 3
        self.image(path, x=x, y=y, w=width, h=height)
        self.set_draw_color(*LINE)
        self.rect(x, y, width, height)
        return width + 2

    def paragraph(self, height, content):
        self.multi_cell(0, height, content, align="L", new_x="LMARGIN", new_y="NEXT")

    def two_lines(self, x, y, width, top, bottom, *, align="L", link=""):
        """Bold value over a muted caption: the unit of every table cell."""
        self.set_xy(x, y + 1)
        self.text_style(9.5, "B")
        self.cell(width, 4.8, top, align=align, link=link)
        self.set_xy(x, y + 5.6)
        self.text_style(8, "", MUTED)
        self.cell(width, 4, bottom, align=align, link=link)


def heading(pdf, request, trips, cities):
    pdf.text_style(24, "B")
    if not cities:
        title = "Nessun volo trovato"
    elif len(cities) == 1:
        title = f"Voli per {cities[0]['name']}"
    else:
        title = "Dove andiamo?"
    pdf.cell(0, 11, title, new_x="LMARGIN", new_y="NEXT")
    pdf.text_style(10.5, "", MUTED)
    pdf.paragraph(5, subtitle(request))
    if trips:
        pdf.paragraph(
            5, f"{checked_label(trips)}. Possono cambiare: ricontrolla dal link prima di comprare."
        )
    else:
        pdf.paragraph(6, "Con questi filtri non ci sono voli da mostrare.")
    pdf.ln(4)


def summary_table(pdf, cities, round_trip):
    """One line per city, to choose at a glance."""
    pdf.text_style(13, "B")
    pdf.cell(0, 8, f"Le mete in breve · {len(cities)} città", new_x="LMARGIN", new_y="NEXT")
    columns = (52, 22, 70, WIDTH - 144)
    labels = ("Città", "Da", "Weekend più economico", "Sul posto" if round_trip else "Durata")

    def column_labels():
        pdf.text_style(8.5, "B", MUTED)
        for width, label in zip(columns, labels, strict=True):
            pdf.cell(width, 6, label)
        pdf.ln(6)

    column_labels()
    for index, item in enumerate(cities):
        best = item["trips"][0]
        origin = city(best["outbound"]["segments"][0]["from_code"])
        minutes = best["useful_minutes"] if round_trip else best["outbound"]["duration_min"]
        # Band and flag are drawn before the text: break the page first, or the
        # row would be split with its flag on one page and its text on the next.
        if pdf.will_page_break(6.4):
            pdf.add_page()
            column_labels()
        if index % 2 == 0:
            pdf.set_fill_color(*BAND)
            pdf.rect(MARGIN, pdf.get_y(), WIDTH, 6.4, style="F")
        y = pdf.get_y()
        shift = pdf.flag(item["country"], MARGIN + 1, y + 1.5, 3.4)
        pdf.set_x(MARGIN + 1 + shift)
        pdf.text_style(10, "B")
        pdf.cell(columns[0] - 1 - shift, 6.4, item["name"])
        pdf.text_style(10, "B", ACCENT)
        pdf.cell(columns[1], 6.4, euro(best["price"]))
        pdf.text_style(9.5)
        pdf.cell(columns[2], 6.4, f"{dates(best)} · da {origin}")
        pdf.cell(columns[3], 6.4, hours(minutes), new_x="LMARGIN", new_y="NEXT")


def city_block(pdf, item, round_trip):
    """City header and its flights; split across pages only when too long for one."""
    bottom = pdf.h - 16
    head = 9 + 5.5
    whole = head + ROW * len(item["trips"]) + 5
    fits_a_page = pdf.t_margin + whole <= bottom
    if pdf.get_y() + (whole if fits_a_page else head + ROW) > bottom:
        pdf.add_page()
    pdf.set_draw_color(*LINE)
    pdf.line(MARGIN, pdf.get_y(), MARGIN + WIDTH, pdf.get_y())
    pdf.ln(1.5)
    shift = pdf.flag(item["country"], MARGIN, pdf.get_y() + 1.2, 4.6)
    pdf.set_x(MARGIN + shift)
    pdf.text_style(14, "B")
    pdf.cell(WIDTH - 40 - shift, 7, item["name"])
    pdf.text_style(14, "B", ACCENT)
    best = euro(item["trips"][0]["price"])
    pdf.cell(40, 7, f"da {best}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.text_style(8.5, "", MUTED)
    found = (
        "1 combinazione trovata" if item["count"] == 1 else f"{item['count']} combinazioni trovate"
    )
    pdf.cell(0, 5, f"{' · '.join(item['codes'])} · {found}", new_x="LMARGIN", new_y="NEXT")
    for index, trip in enumerate(item["trips"]):
        if pdf.get_y() + ROW > bottom:
            pdf.add_page()
        y = pdf.get_y()
        if index == 0:
            pdf.set_fill_color(*HIGHLIGHT)
            pdf.rect(MARGIN, y + 0.5, COLUMNS[0] - 3, ROW - 2.5, style="F")
        origin = trip["outbound"]["segments"][0]["from_code"]
        home = trip["inbound"]["segments"][-1]["to_code"] if trip["inbound"] else origin
        where = f"da {origin}" if home == origin else f"{origin}/{home}"
        pdf.two_lines(MARGIN + 1.5, y, COLUMNS[0] - 4, euro(trip["price"]), where)
        x = MARGIN + COLUMNS[0]
        legs = (("Andata", trip["outbound"]), ("Ritorno", trip["inbound"]))
        for (label, leg), width in zip(legs, COLUMNS[1:3], strict=True):
            if leg:
                times, carrier = leg_lines(leg)
                link = leg.get("booking_url", "")
                pdf.two_lines(x, y, width - 2, times, f"{label} · {carrier}", link=link)
            x += width
        if round_trip:
            pdf.two_lines(x, y, COLUMNS[3], hours(trip["useful_minutes"]), "sul posto", align="R")
        pdf.set_xy(MARGIN, y + ROW)
    pdf.ln(3)


def build_pdf(request, trips, per_city=PER_CITY):
    cities = by_city(trips, per_city)
    round_trip = request["trip_type"] != "one_way"
    pdf = Document()
    pdf.set_title("Voli trovati")
    pdf.add_page()
    heading(pdf, request, trips, cities)
    if not cities:
        return bytes(pdf.output())
    summary_table(pdf, cities, round_trip)
    pdf.add_page()
    pdf.text_style(13, "B")
    pdf.cell(0, 8, "I voli, città per città", new_x="LMARGIN", new_y="NEXT")
    pdf.text_style(9, "", MUTED)
    notes = "Il prezzo somma due biglietti di sola andata separati e include solo il bagaglio base."
    if round_trip:
        notes += ' "Sul posto" sono le ore tra l\'atterraggio e il decollo del ritorno.'
    pdf.paragraph(4.6, notes + " Tocca un volo per aprirlo su Google Flights.")
    pdf.ln(3)
    for item in cities:
        city_block(pdf, item, round_trip)
    return bytes(pdf.output())
