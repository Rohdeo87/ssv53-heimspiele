"""Header-based parsers for PUBLIC nuLiga and KSV team data.

No player lists, contacts, login forms, referees or match reports are imported.
Unknown layouts raise ParseError instead of publishing guessed/empty data.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

HB_HOST = "hvbrandenburg-handball.liga.nu"
VB_HOST = "ksv-volleyball-oberhavel.de"
HB_CLUB = f"https://{HB_HOST}/cgi-bin/WebObjects/nuLigaHBDE.woa/wa/clubTeams?club=33547"
VB_MAIN = f"https://{VB_HOST}/mixed_2.html"
BERLIN = ZoneInfo("Europe/Berlin")


class ParseError(ValueError):
    pass


def text(node: Tag | None) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True) if node else "").strip()


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold()).replace("ß", "ss")
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in value if not unicodedata.combining(c)))


def is_ssv(value: str) -> bool:
    # Require the full club name, not merely "SSV" or the opponent's club name.
    return bool(re.fullmatch(r"sch(?:o|oe)nwaldersv(?:19)?53(?:i{1,3}|[1-3])?", norm(value)))


def safe_source_url(url: str, base: str) -> str:
    resolved = urljoin(base, url)
    p = urlsplit(resolved)
    if (p.scheme != "https" or p.hostname not in {HB_HOST, VB_HOST}
            or p.username or p.password or p.port not in {None, 443}):
        raise ParseError("Nicht freigegebene Quellen-URL.")
    return resolved


def rows(table: Tag) -> list[list[Tag | None]]:
    """Expand rowspan/colspan; ignore rows of nested layout tables."""
    result: list[list[Tag | None]] = []
    carry: dict[int, tuple[Tag, int]] = {}
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        line: list[Tag | None] = []
        next_carry: dict[int, tuple[Tag, int]] = {}
        ci = 0

        def advance() -> None:
            nonlocal ci
            while ci in carry:
                node, remaining = carry[ci]
                line.append(node)
                if remaining > 1:
                    next_carry[ci] = (node, remaining - 1)
                ci += 1

        for cell in tr.find_all(["td", "th"], recursive=False):
            advance()
            try:
                colspan = int(cell.get("colspan", 1))
                rowspan = int(cell.get("rowspan", 1))
            except (TypeError, ValueError) as exc:
                raise ParseError("Ungültige Tabellenzellen.") from exc
            if not 1 <= colspan <= 30 or not 1 <= rowspan <= 200:
                raise ParseError("Unerwartete Tabellengröße.")
            for _ in range(colspan):
                line.append(cell)
                if rowspan > 1:
                    next_carry[ci] = (cell, rowspan - 1)
                ci += 1
        # Preserve carried cells after the last explicit cell.
        while carry and ci <= max(carry):
            if ci in carry:
                advance()
            else:
                line.append(None)
                ci += 1
        carry = next_carry
        if line:
            result.append(line)
    return result


def locate(soup: BeautifulSoup, aliases: dict[str, set[str]], required: set[str]):
    """Find a semantic header, including sites that use td instead of th."""
    found = []
    for table in soup.find_all("table"):
        grid = rows(table)
        for ri, row in enumerate(grid[:12]):
            index = {}
            for ci, cell in enumerate(row):
                label = norm(text(cell))
                for key, variants in aliases.items():
                    if label in variants and key not in index:
                        index[key] = ci
            if required <= index.keys():
                found.append((table, grid[ri + 1:], index))
                break
    if len(found) > 1:
        raise ParseError("Mehrere passende Tabellen; Quelle muss geprüft werden.")
    return found[0] if found else None


def cell(row, index, key):
    i = index.get(key)
    return row[i] if i is not None and i < len(row) else None


def int_value(value: str, allow_negative: bool = False) -> int:
    expression = r"[+-]?\d+" if allow_negative else r"\d+"
    if not re.fullmatch(expression, value):
        raise ParseError(f"Ungültiger Tabellenwert ({value[:25]}).")
    number = int(value)
    if abs(number) > 100000:
        raise ParseError("Unplausibler Tabellenwert.")
    return number


def pair(value: str) -> list[int] | None:
    m = re.fullmatch(r"\s*(\d{1,5})\s*:\s*(\d{1,5})\s*", value)
    return [int(m[1]), int(m[2])] if m else None


def date_fields(raw_date: str, raw_time: str = "") -> dict:
    # Dates remain Europe/Berlin; unknown times are not silently set to midnight.
    d = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})\b", raw_date)
    if not d:
        return {"date": None, "time": None, "startsAt": None}
    year = int(d[3]); year = year + 2000 if year < 100 else year
    try:
        base = datetime(year, int(d[2]), int(d[1]), tzinfo=BERLIN)
        t = re.search(r"(?<!\d)([0-2]?\d):([0-5]\d)(?::[0-5]\d)?(?!\d)", raw_time or raw_date)
        hhmm = None
        start = None
        if t:
            dt = base.replace(hour=int(t[1]), minute=int(t[2]))
            hhmm = dt.strftime("%H:%M")
            start = dt.isoformat()
        return {"date": base.date().isoformat(), "time": hhmm, "startsAt": start}
    except ValueError as exc:
        raise ParseError("Ungültige Datums-/Zeitangabe.") from exc


def season_guard(matches: list[dict], start: int) -> None:
    for item in matches:
        if item["date"] and not f"{start}-07-01" <= item["date"] <= f"{start + 1}-06-30":
            raise ParseError("Spiel außerhalb der ausgewählten Saison.")


def deduplicate(items: list[dict]) -> list[dict]:
    output = {}
    for item in items:
        if item["id"] in output and output[item["id"]] != item:
            raise ParseError("Widersprüchliche doppelte Spielnummer.")
        output[item["id"]] = item
    return list(output.values())


def handball_teams(html: str, season_start: int) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    if "Schönwalder" not in soup.get_text():
        raise ParseError("Vereinsseite ist nicht eindeutig dem SSV zugeordnet.")
    teams = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "groupPage" not in href:
            continue
        url = safe_source_url(href, HB_CLUB)
        if urlsplit(url).hostname != HB_HOST:
            raise ParseError("Falsche Handballquelle.")
        query = parse_qs(urlsplit(url).query)
        championship = query.get("championship", [""])[0]
        years = re.findall(r"20\d{2}", championship)
        if str(season_start) not in years or str(season_start + 1) not in years:
            continue
        group = query.get("group", [""])[0]
        tr = anchor.find_parent("tr")
        if not re.fullmatch(r"\d+", group) or not tr:
            continue
        cells = tr.find_all(["td", "th"], recursive=False)
        team_name = text(cells[0]) if cells else ""
        league = text(anchor)
        if not team_name or len(team_name) > 100 or not league:
            raise ParseError("Mannschaftszuordnung unklar.")
        # Deliberately do not read the contacts / officials column.
        label = team_name + (" · Pokal" if "pokal" in championship.casefold() else "")
        record = {"id": f"hb-{group}", "groupId": group, "label": label,
                  "league": league, "season": f"{season_start}/{str(season_start + 1)[-2:]}",
                  "sourceUrl": url}
        if group in teams and teams[group] != record:
            raise ParseError("Mehrdeutige Staffelzuordnung.")
        teams[group] = record
    if not teams or len(teams) > 30:
        raise ParseError("Keine eindeutige Mannschaftsliste für die aktuelle Saison.")
    return list(teams.values())


def handball_schedule_url(html: str, base: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    urls = {safe_source_url(a["href"], base) for a in soup.find_all("a", href=True)
            if norm(text(a)) == "spielplangesamt"}
    if not urls and "pokal" in unquote(base).casefold() and any(
        norm(text(h)) == "spielplan" for h in soup.find_all(["h2", "h3"])
    ):
        return base  # Cup pages publish their complete round directly.
    if len(urls) != 1:
        raise ParseError("Vollständiger Spielplan nicht eindeutig verlinkt.")
    url = urls.pop()
    original = parse_qs(urlsplit(base).query)
    candidate = parse_qs(urlsplit(url).query)
    if urlsplit(url).hostname != HB_HOST or candidate.get("group") != original.get("group"):
        raise ParseError("Gesamtspielplan gehört zu anderer Staffel.")
    return url


HB_TABLE_HEADERS = {
    "rank": {"rang", "platz"}, "name": {"mannschaft"},
    "played": {"begegnungen", "spiele", "sp"}, "won": {"s", "siege"},
    "drawn": {"u", "unentschieden"}, "lost": {"n", "niederlagen"},
    "goals": {"tore"}, "difference": {"", "differenz", "diff"}, "points": {"punkte"},
}


def handball_standings(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    # '+'/'-' normalize to an empty string; resolve this column separately below.
    aliases = {k: v for k, v in HB_TABLE_HEADERS.items() if k != "difference"}
    found = locate(soup, aliases, {"rank", "name", "played", "points", "goals"})
    if not found:
        headings = [norm(text(h)) for h in soup.find_all(["h2", "h3"])]
        if "tabelle" in headings:
            raise ParseError("Tabellenüberschrift vorhanden, Datenstruktur nicht erkannt.")
        participants = locate(soup, {"name": {"mannschaft"}, "raster": {"raster"}}, {"name", "raster"})
        if participants:
            _, grid, ix = participants
            names = [text(cell(r, ix, "name")) for r in grid if text(cell(r, ix, "name"))]
            if not names or not any(is_ssv(n) for n in names):
                raise ParseError("Teilnehmerliste enthält keinen SSV.")
            return {"kind": "participants", "rows": [{"name": n, "isSSV": is_ssv(n)} for n in names],
                    "message": "Die Quelle veröffentlicht eine Teilnehmerliste, keine Rangliste."}
        return {"kind": "unavailable", "rows": [],
                "message": "Für diesen Wettbewerb ist keine auslesbare Tabelle veröffentlicht."}
    _, grid, ix = found
    output = []
    for row in grid:
        rank_raw = text(cell(row, ix, "rank"))
        if not rank_raw or norm(rank_raw) in {"rang", "platz"}:
            continue
        if not re.fullmatch(r"\d+\.?", rank_raw):
            # Non-team explanatory footer rows are skipped, not numeric rows.
            if len(set(text(c) for c in row)) == 1:
                continue
            raise ParseError("Tabellenrang nicht erkannt.")
        name = text(cell(row, ix, "name"))
        withdrawn = re.search(r"zurückgezogen(?: am \d{2}\.\d{2}\.\d{4})?", " ".join(text(c) for c in row), re.I)
        if withdrawn:
            output.append({"rank": int(rank_raw.rstrip(".")), "name": name, "isSSV": is_ssv(name),
                           "played": None, "won": None, "drawn": None, "lost": None,
                           "goals": None, "points": None, "difference": None,
                           "status": "withdrawn", "note": withdrawn[0]})
            continue
        goals = pair(text(cell(row, ix, "goals")))
        points = pair(text(cell(row, ix, "points")))
        if not name or goals is None or points is None:
            raise ParseError("Unvollständige Tabellenzeile.")
        item = {"rank": int(rank_raw.rstrip(".")), "name": name, "isSSV": is_ssv(name),
                "played": int_value(text(cell(row, ix, "played"))),
                "goals": goals, "points": f"{points[0]}:{points[1]}",
                "difference": goals[0] - goals[1]}
        for field in ["won", "drawn", "lost"]:
            item[field] = int_value(text(cell(row, ix, field))) if field in ix else None
        if all(item[k] is not None for k in ["won", "drawn", "lost"]):
            if sum(item[k] for k in ["won", "drawn", "lost"]) != item["played"]:
                raise ParseError("Tabellenstatistik ist widersprüchlich.")
        output.append(item)
    if not output or len(output) > 40 or not any(r["isSSV"] for r in output):
        raise ParseError("Tabelle fehlt oder enthält keine SSV-Mannschaft.")
    if len({r["name"] for r in output}) != len(output):
        raise ParseError("Mannschaft doppelt in der Tabelle.")
    return {"kind": "table", "rows": output, "message": "Offizielle Tabellenreihenfolge."}


HB_MATCH_HEADERS = {
    "date": {"datum"}, "time": {"zeit", "uhrzeit"}, "venue": {"ort", "halle"},
    "number": {"nr", "spielnr", "spielnummer"}, "home": {"heimmannschaft", "heimteam"},
    "away": {"gastmannschaft", "gastteam"}, "result": {"ergebnis", "resultat"},
}


def handball_matches(html: str, group: str, base: str, season_start: int, festivals: bool = False) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found = locate(soup, HB_MATCH_HEADERS, {"date", "time", "number", "home", "away"})
    if not found:
        # A deliberately empty official schedule must explicitly say so.
        if re.search(r"keine (?:begegnungen|spiele|spielansetzungen)", soup.get_text(), re.I):
            return []
        raise ParseError("Spielplantabelle nicht erkannt.")
    _, grid, ix = found
    last_date = ""
    output = []
    recognized = 0
    for row in grid:
        number = text(cell(row, ix, "number"))
        home, away = text(cell(row, ix, "home")), text(cell(row, ix, "away"))
        date_raw = text(cell(row, ix, "date"))
        if re.search(r"\d{1,2}\.\d{1,2}\.\d{2,4}", date_raw):
            last_date = date_raw
        elif date_raw and norm(date_raw) not in {"datum"}:
            last_date = ""  # Explicit unknown date: never inherit another day's date.
        if not number or norm(number) in {"nr", "spielnr", "spielnummer"}:
            continue
        if not re.fullmatch(r"\d+", number):
            if home and away and (is_ssv(home) or is_ssv(away)):
                raise ParseError("SSV-Spielnummer nicht erkannt.")
            continue
        if not home or not away:
            raise ParseError("Spiel ohne Heim- oder Gastmannschaft.")
        recognized += 1
        festival = festivals and norm(away) == "angemeldetemannschaften"
        if not (is_ssv(home) or is_ssv(away) or festival) or norm(home) == "spielfrei" or norm(away) == "spielfrei":
            continue
        result_idx = ix.get("result", ix["away"] + 1)
        result_cell = row[result_idx] if result_idx < len(row) else None
        result_text = text(result_cell)
        score_match = re.search(r"(?<!\d)(\d{1,3})\s*:\s*(\d{1,3})(?!\d)", result_text)
        score = [int(score_match[1]), int(score_match[2])] if score_match else None
        time_raw = text(cell(row, ix, "time"))
        state = "result" if score is not None else "scheduled"
        # Only explicit status words, never referee abbreviations, are interpreted.
        status_text = " ".join([date_raw, time_raw, result_text])
        status_note = ""
        for word, status in [("abgesagt", "cancelled"), ("ausgefallen", "cancelled"),
                             ("verlegt", "postponed"), ("termin offen", "postponed")]:
            if word in status_text.casefold():
                state = status; status_note = word.capitalize(); break
        if score is None and result_text in {"WG", "WH", "NG", "NH", "ZG", "ZH"}:
            status_note = "Quellenstatus: " + result_text
            state = "status"
        venue_cell = cell(row, ix, "venue")
        venue_link = venue_cell.find("a", href=True) if venue_cell else None
        venue = {"label": "Halle " + text(venue_cell), "url": None} if text(venue_cell) else None
        if venue_link:
            venue["url"] = safe_source_url(venue_link["href"], base)
            title = str(venue_link.get("title", "")).strip()
            if title and "@" not in title and len(title) < 180:
                venue["label"] = title
        identifier = f"hb-{group}-{number}"
        if festival:
            import hashlib
            identifier += "-" + hashlib.sha256((last_date + home).encode()).hexdigest()[:12]
            status_note = "Staffeltermin. Die Teilnahme des SSV bitte mit dem Trainerteam klären."
        item = {"id": identifier, "number": number, "home": home, "away": away,
                "kind": "festival" if festival else "match", "isHome": None if festival else is_ssv(home), "score": score, "sets": [], "state": state,
                "note": status_note, "venue": venue, "sourceUrl": base,
                **date_fields(last_date, time_raw)}
        output.append(item)
    if not recognized:
        raise ParseError("Spielplan ohne erkennbare Spielzeilen.")
    if not output:
        raise ParseError("Gesamtspielplan enthält keine SSV-Begegnungen.")
    season_guard(output, season_start)
    return deduplicate(output)


def volleyball_links(html: str, season_start: int) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    if not any("2. Kreisklasse Mixed" in text(h) for h in soup.find_all(["h1", "h2", "h3"])):
        raise ParseError("Volleyball-Liga nicht eindeutig erkannt.")
    season = f"{str(season_start)[-2:]}/{str(season_start + 1)[-2:]}"
    urls = set()
    for a in soup.find_all("a", href=True):
        if "ergebnisse" in text(a).casefold() and season in text(a):
            target = safe_source_url(a["href"], VB_MAIN)
            if urlsplit(target).hostname == VB_HOST and urlsplit(target).path.startswith("/mixed_2/"):
                urls.add(target)
    if len(urls) != 1:
        raise ParseError("Aktuelle Volleyball-Ergebnisse nicht eindeutig verlinkt.")
    image_urls = []
    headings = [h for h in soup.find_all(["h1", "h2", "h3"]) if "2. Kreisklasse Mixed" in text(h)]
    # Only inspect the content after this exact league heading, never nav/logo images.
    for node in headings[0].find_all_next():
        if node.name == "table":
            break
        if node.name == "img" and node.get("src"):
            target = safe_source_url(node["src"], VB_MAIN)
            if urlsplit(target).hostname == VB_HOST and urlsplit(target).path.startswith("/file/i/"):
                image_urls.append(target)
    if len(set(image_urls)) != 1:
        raise ParseError("Offizielle Volleyball-Tabellengrafik nicht eindeutig erkannt.")
    published = re.search(r"Stand:\s*(\d{2}\.\d{2}\.\d{4})", soup.get_text(" "))
    return {"resultsUrl": urls.pop(), "imageUrl": image_urls[0],
            "sourcePublishedAt": published[1] if published else None}


VB_MATCH_HEADERS = {
    "datetime": {"datumundzeit", "datumunduhrzeit"}, "number": {"nr", "spielnr"},
    "home": {"heimteam", "heimmannschaft"}, "away": {"gastteam", "gastmannschaft"},
    "result": {"ergebnis"}, "sets": {"satze", "satzstande"},
}


def volleyball_matches(html: str, base: str, season_start: int) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found = locate(soup, VB_MATCH_HEADERS, {"datetime", "number", "home", "away", "result"})
    if not found:
        raise ParseError("Volleyball-Ergebnistabelle nicht erkannt.")
    _, grid, ix = found
    output = []
    for row in grid:
        number = text(cell(row, ix, "number"))
        if not re.fullmatch(r"\d+", number):
            continue  # Matchday headings have no game number.
        home, away = text(cell(row, ix, "home")), text(cell(row, ix, "away"))
        if not home or not away:
            raise ParseError("Unvollständige Volleyball-Begegnung.")
        if not (is_ssv(home) or is_ssv(away)):
            continue
        raw_result = text(cell(row, ix, "result"))
        score = pair(raw_result)
        state = "result" if score is not None else "scheduled"
        note = ""
        if score and not (0 <= min(score) <= 2 and max(score) == 3):
            raise ParseError("Volleyball-Ergebnis ist kein plausibles Endergebnis.")
        if score is None and raw_result not in {"", "-", "–", "—"}:
            note = "Quelle: " + raw_result
            if re.search(r"abgesagt|ausgefallen", raw_result, re.I):
                state = "cancelled"
            elif re.search(r"verlegt|verschoben", raw_result, re.I):
                state = "postponed"
        sets = [[int(a), int(b)] for a, b in re.findall(r"(\d{1,3})\s*:\s*(\d{1,3})", text(cell(row, ix, "sets")))]
        if score and sets and len(sets) != sum(score):
            raise ParseError("Satzstände widersprechen dem Endergebnis.")
        item = {"id": f"vb-{season_start}-{number}", "number": number, "home": home, "away": away,
                "isHome": is_ssv(home), "score": score, "sets": sets, "state": state,
                "note": note, "venue": None, "sourceUrl": base,
                **date_fields(text(cell(row, ix, "datetime")))}
        output.append(item)
    if not output:
        raise ParseError("Keine SSV-Spiele in der Volleyball-Liga erkannt.")
    season_guard(output, season_start)
    return deduplicate(output)
