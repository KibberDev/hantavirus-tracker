#!/usr/bin/env python3
"""
HantaTracker — Scraper automático.
Se ejecuta via GitHub Actions cada 15 min.
Detecta de forma independiente:
  - Casos confirmados (ES + EN, múltiples patrones)
  - Fallecimientos (independientemente de los casos)
  - Recuperaciones / altas
  - Atribución por país cuando el artículo menciona un único país
  - Auto-extensión del timeline cuando hay cambios
"""
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser

ROOT      = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data.json"

RSS_SOURCES = [
    # Fuentes con marca: van primero para ganar el dedup y mostrar el medio real
    ("WHO DON",        "https://www.who.int/feeds/entity/don/en/rss.xml"),
    ("ProMED",         "https://promedmail.org/feed/"),
    ("BBC Health",     "https://feeds.bbci.co.uk/news/health/rss.xml"),
    ("NPR Health",     "https://feeds.npr.org/1128/rss.xml"),
    ("Antena 3",       "https://news.google.com/rss/search?q=hantavirus+site:antena3.com&hl=es&gl=ES&ceid=ES:es"),
    ("RTVE",           "https://news.google.com/rss/search?q=hantavirus+site:rtve.es&hl=es&gl=ES&ceid=ES:es"),
    ("El País",        "https://news.google.com/rss/search?q=hantavirus+site:elpais.com&hl=es&gl=ES&ceid=ES:es"),
    ("El Mundo",       "https://news.google.com/rss/search?q=hantavirus+site:elmundo.es&hl=es&gl=ES&ceid=ES:es"),
    ("La Vanguardia",  "https://news.google.com/rss/search?q=hantavirus+site:lavanguardia.com&hl=es&gl=ES&ceid=ES:es"),
    ("ABC",            "https://news.google.com/rss/search?q=hantavirus+site:abc.es&hl=es&gl=ES&ceid=ES:es"),
    # Catch-all genéricos al final
    ("Google News ES", "https://news.google.com/rss/search?q=hantavirus&hl=es&gl=ES&ceid=ES:es"),
    ("Google News EN", "https://news.google.com/rss/search?q=hantavirus&hl=en&gl=US&ceid=US:en"),
]

KEYWORDS = ["hanta", "mv hondius", "cepa andes", "andes strain"]

# Marcadores que indican que el artículo trata el brote ACTUAL (2026, MV Hondius)
# Sin al menos uno de estos, NO extraemos números — evita falsos positivos
# de artículos sobre brotes históricos (Patagonia 2018, etc.)
CURRENT_OUTBREAK_MARKERS = [
    # Marcadores específicos del brote MV Hondius — sin uno de estos
    # NO se extraen cifras (evita confundir con casos endémicos en Argentina,
    # Chile, etc., que no tienen relación con este brote concreto)
    "mv hondius", "hondius",
    "crucero", "cruise ship", "cruise",
    "brote actual", "current outbreak", "este brote",
    "transatlantic", "transatlántico",
    "canarias", "tenerife", "gran canaria", "las palmas",
    "cabo verde",
]

# Años históricos: si aparecen cerca del número, el contexto es brote pasado
HISTORICAL_YEAR_RE = re.compile(r"\b(?:19\d{2}|20[01]\d|202[0-5])\b")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HantaTracker/1.0; +https://github.com/KibberDev/hantavirus-tracker)"
}

CASE_PATTERNS = [
    # English
    r"(\d+)\s+(?:laboratory[\-\s]?confirmed\s+)?(?:human\s+)?cases?",
    r"total\s+(?:of\s+)?(\d+)\s+cases?",
    r"(\d+)\s+persons?\s+(?:infected|affected|confirmed)",
    r"(\d+)\s+(?:passengers?|crew\s+members?|people)\s+(?:tested\s+)?(?:positive|infected)",
    r"confirmed[:\s]+(\d+)",
    r"(?:cases?\s+)?(?:rises?|rose)\s+to\s+(\d+)",
    r"climb(?:s|ed)?\s+to\s+(\d+)",
    r"reach(?:es|ed)?\s+(\d+)\s+cases?",
    # Spanish
    r"(\d+)\s+casos?\s+(?:confirmados?|sospechosos?|positivos?)",
    r"casos?\s+(?:confirmados?|sospechosos?)\s*(?:asciende[n]?\s+a|suben?\s+a|llegan?\s+a)?\s*(?:de\s+|a\s+)?(\d+)",
    r"(?:asciende[n]?\s+a|suben?\s+a|llegan?\s+a|son\s+ya)\s+(\d+)\s+(?:los\s+)?(?:casos?|infectados?|positivos?|contagiados?)",
    r"ya\s+son\s+(\d+)\s+(?:casos?|infectados?|positivos?|contagiados?)",
    r"(\d+)\s+(?:infectados?|contagiados?|positivos?)",
    r"(?:total\s+de\s+|cifra\s+de\s+)(\d+)\s+(?:casos?|infectados?|contagiados?)",
]

DEATH_PATTERNS = [
    # English
    r"(\d+)\s+(?:deaths?|fatalities?|fatal\s+cases?)",
    r"(\d+)\s+(?:persons?\s+)?(?:have\s+)?died",
    r"death\s+toll\s+(?:rises?|climbs?|reaches?)\s+(?:to\s+)?(\d+)",
    r"(\d+)\s+dead",
    # Spanish
    r"(\d+)\s+(?:fallecidos?|muertos?|víctimas?\s+mortales?|fatales?)",
    r"(?:fallecidos?|muertos?|víctimas?)\s*(?:asciende[n]?\s+a|suben?\s+a|llegan?\s+a)\s+(\d+)",
    r"(\d+)\s+han\s+(?:fallecido|muerto)",
    r"ya\s+son\s+(\d+)\s+(?:fallecidos?|muertos?|víctimas?)",
    r"se\s+(?:eleva|incrementa)\s+a\s+(\d+)\s+(?:fallecidos?|muertos?)",
]

RECOVERED_PATTERNS = [
    # English
    r"(\d+)\s+(?:patients?\s+)?recovered",
    r"(\d+)\s+(?:patients?\s+)?(?:have\s+)?(?:been\s+)?discharged",
    r"(\d+)\s+(?:patients?\s+)?(?:cured|healed)",
    r"recoveries?\s+(?:rise|climb|reach|stand)\s+(?:to\s+|at\s+)?(\d+)",
    # Spanish
    r"(\d+)\s+(?:pacientes?\s+)?(?:dados?\s+de\s+alta|recuperados?|curados?|sanados?)",
    r"(?:dados?\s+de\s+alta|recuperados?|curados?)\s*(?:asciende[n]?\s+a|son|suman)\s+(\d+)",
    r"(\d+)\s+han\s+(?:sido\s+)?(?:dados?\s+de\s+alta|recuperados?|curados?)",
    r"ya\s+(?:son|hay)\s+(\d+)\s+(?:dados?\s+de\s+alta|recuperados?|curados?)",
]

COUNTRY_DATA = {
    "AR": {"name": "Argentina", "iso_num": "032",
           "aliases": ["argentina", "argentino", "argentinian", "buenos aires"]},
    "NL": {"name": "Países Bajos", "iso_num": "528",
           "aliases": ["netherlands", "holland", "holanda", "países bajos",
                       "paises bajos", "dutch", "neerland", "amsterdam"]},
    "DE": {"name": "Alemania", "iso_num": "276",
           "aliases": ["germany", "alemania", "german", "alemán", "alemana",
                       "alemanes", "berlin", "berlín", "munich", "múnich"]},
    "ZA": {"name": "Sudáfrica", "iso_num": "710",
           "aliases": ["south africa", "sudáfrica", "sudafrica", "south african",
                       "johannesburgo", "johannesburg", "ciudad del cabo", "cape town"]},
    "ES": {"name": "España", "iso_num": "724",
           "aliases": ["spain", "españa", "spanish", "español", "española",
                       "españoles", "canarias", "tenerife", "gran canaria",
                       "las palmas", "galicia", "madrid", "barcelona",
                       "valencia", "sevilla", "manises"]},
    "IT": {"name": "Italia", "iso_num": "380",
           "aliases": ["italy", "italia", "italian", "italiano", "italiana",
                       "roma", "milán", "rome", "milan"]},
    "FR": {"name": "Francia", "iso_num": "250",
           "aliases": ["france", "francia", "french", "francés", "francesa",
                       "parís", "paris", "marseille"]},
    "GB": {"name": "Reino Unido", "iso_num": "826",
           "aliases": ["united kingdom", "britain", "british", "reino unido",
                       "inglaterra", "england", "londres", "london"]},
    "US": {"name": "Estados Unidos", "iso_num": "840",
           "aliases": ["united states", "estados unidos", "ee.uu.", "eeuu",
                       "u.s.a", "usa", "american", "americano"]},
    "PT": {"name": "Portugal", "iso_num": "620",
           "aliases": ["portugal", "portuguese", "portugués", "portuguesa",
                       "lisboa", "lisbon", "oporto"]},
    "BE": {"name": "Bélgica", "iso_num": "056",
           "aliases": ["belgium", "bélgica", "belgica", "belga",
                       "bruselas", "brussels"]},
    "CH": {"name": "Suiza", "iso_num": "756",
           "aliases": ["switzerland", "suiza", "swiss", "suizo",
                       "ginebra", "geneva", "zurich"]},
    "AT": {"name": "Austria", "iso_num": "040",
           "aliases": ["austria", "austrian", "austriaco", "viena", "vienna"]},
    "IE": {"name": "Irlanda", "iso_num": "372",
           "aliases": ["ireland", "irlanda", "irish", "irlandés", "irlandesa",
                       "dublín", "dublin"]},
    "SE": {"name": "Suecia", "iso_num": "752",
           "aliases": ["sweden", "suecia", "swedish", "sueco", "sueca",
                       "estocolmo", "stockholm"]},
    "NO": {"name": "Noruega", "iso_num": "578",
           "aliases": ["norway", "noruega", "norwegian", "noruego", "oslo"]},
    "DK": {"name": "Dinamarca", "iso_num": "208",
           "aliases": ["denmark", "dinamarca", "danish", "danés", "danesa",
                       "copenhague", "copenhagen"]},
    "FI": {"name": "Finlandia", "iso_num": "246",
           "aliases": ["finland", "finlandia", "finnish", "finlandés",
                       "helsinki"]},
    "GR": {"name": "Grecia", "iso_num": "300",
           "aliases": ["greece", "grecia", "greek", "griego", "griega",
                       "atenas", "athens"]},
    "PL": {"name": "Polonia", "iso_num": "616",
           "aliases": ["poland", "polonia", "polish", "polaco", "polaca",
                       "varsovia", "warsaw"]},
    "BR": {"name": "Brasil", "iso_num": "076",
           "aliases": ["brazil", "brasil", "brazilian", "brasileño",
                       "brasileña", "são paulo", "rio de janeiro"]},
    "CL": {"name": "Chile", "iso_num": "152",
           "aliases": ["chile", "chilean", "chileno", "chilena", "santiago de chile"]},
    "MX": {"name": "México", "iso_num": "484",
           "aliases": ["mexico", "méxico", "mexican", "mexicano", "mexicana"]},
    "CA": {"name": "Canadá", "iso_num": "124",
           "aliases": ["canada", "canadá", "canadian", "canadiense"]},
    "AU": {"name": "Australia", "iso_num": "036",
           "aliases": ["australia", "australian", "australiano", "australiana"]},
    "JP": {"name": "Japón", "iso_num": "392",
           "aliases": ["japan", "japón", "japanese", "japonés", "japonesa",
                       "tokio", "tokyo"]},
    "CN": {"name": "China", "iso_num": "156",
           "aliases": ["china", "chinese", "chino", "pekín", "beijing", "shanghai"]},
    "KR": {"name": "Corea del Sur", "iso_num": "410",
           "aliases": ["south korea", "corea del sur", "korean", "coreano",
                       "coreana", "seúl", "seoul"]},
    "MA": {"name": "Marruecos", "iso_num": "504",
           "aliases": ["morocco", "marruecos", "moroccan", "marroquí",
                       "casablanca", "rabat"]},
    "CV": {"name": "Cabo Verde", "iso_num": "132",
           "aliases": ["cape verde", "cabo verde", "praia"]},
}

# Vista plana de aliases para detección rápida
COUNTRY_ALIASES = {code: c["aliases"] for code, c in COUNTRY_DATA.items()}


def clean(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def has_historical_context(text: str, match_start: int, match_end: int, window: int = 80) -> bool:
    """True si en una ventana alrededor del match aparece un año histórico (1900-2025)."""
    ctx = text[max(0, match_start - window):min(len(text), match_end + window)]
    return bool(HISTORICAL_YEAR_RE.search(ctx))


def is_current_outbreak_article(text: str) -> bool:
    """True si el artículo claramente habla del brote actual del MV Hondius."""
    return any(marker in text for marker in CURRENT_OUTBREAK_MARKERS)


def extract_first_number(text: str, patterns: list[str], min_val: int = 0, max_val: int = 10000) -> int | None:
    """Primer número plausible que encaje con un patrón Y no esté en contexto histórico."""
    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            try:
                n = int(m.group(1))
                if not (min_val <= n <= max_val):
                    continue
                if has_historical_context(text, m.start(), m.end()):
                    continue  # número junto a año histórico — descartamos
                return n
            except (ValueError, IndexError):
                continue
    return None


def _country_alt(aliases: list[str]) -> str:
    """Construye alternancia regex con los aliases de un país."""
    return r"(?:" + "|".join(re.escape(a) for a in aliases) + r")"


def extract_country_metrics(text: str) -> dict[str, dict]:
    """
    Atribución estricta por país. Solo aplica si el país es:
      - sujeto del verbo de notificar/confirmar/registrar, o
      - va precedido de "in/en" tras la cifra
    Esto evita falsos positivos como "la cepa Andes (originaria de Argentina)
    causó 3 muertes en el crucero" — donde Argentina aparece pero no es la
    localización de las muertes.
    """
    results: dict[str, dict] = {}

    case_words   = r"(?:cases?|casos?|infections?|infectados?|positivos?|contagiados?|pacientes?)"
    death_words  = r"(?:deaths?|fatalities?|fallecidos?|muertos?|muertes?|víctimas?\s+mortales?)"
    verb_report  = (
        r"(?:reports?|confirms?|registers?|records?|notifies|sees|adds|"
        r"has|now\s+has|suma|notifica|confirma|registra|reporta|añade|"
        r"tiene\s+ya?|cuenta\s+con|eleva\s+a|incrementa\s+a)"
    )
    location_prep = (
        r"(?:in|reported\s+in|confirmed\s+in|en|notificados?\s+en|"
        r"confirmados?\s+en|registrados?\s+en|reportados?\s+en|detectados?\s+en)"
    )

    for code, info in COUNTRY_DATA.items():
        c = _country_alt(info["aliases"])
        max_cases = 0
        max_deaths = 0

        case_patterns = [
            # País + verbo + N + casos
            rf"\b{c}\s+{verb_report}\s+(?:its\s+(?:first|new)\s+|sus\s+(?:primeros?\s+|nuevos?\s+))?(\d+)\s+(?:new\s+|nuevos?\s+|confirmed\s+|confirmados?\s+)?{case_words}",
            # N + casos + en + país
            rf"\b(\d+)\s+(?:new\s+|nuevos?\s+|confirmed\s+|confirmados?\s+)?{case_words}\s+{location_prep}\s+{c}\b",
        ]
        first_case_patterns = [
            # Primer caso en {país}
            rf"\b(?:first\s+case|primer\s+caso)\s+{location_prep}\s+{c}\b",
            # {País} confirma su primer caso
            rf"\b{c}\s+{verb_report}\s+(?:its\s+|su\s+)?(?:first\s+case|primer\s+caso)\b",
        ]
        death_patterns = [
            rf"\b{c}\s+{verb_report}\s+(\d+)\s+{death_words}",
            rf"\b(\d+)\s+{death_words}\s+{location_prep}\s+{c}\b",
        ]
        first_death_patterns = [
            rf"\b(?:first\s+death|primer\s+(?:fallecido|muerto|fallecimiento))\s+{location_prep}\s+{c}\b",
            rf"\b{c}\s+{verb_report}\s+(?:its\s+|su\s+)?(?:first\s+death|primer\s+(?:fallecido|muerto|fallecimiento))\b",
        ]

        for pat in case_patterns:
            for m in re.finditer(pat, text, re.I):
                if has_historical_context(text, m.start(), m.end()):
                    continue
                try:
                    n = int(m.group(1))
                    if 1 <= n <= 10000 and n > max_cases:
                        max_cases = n
                except (ValueError, IndexError):
                    continue

        for pat in first_case_patterns:
            for m in re.finditer(pat, text, re.I):
                if has_historical_context(text, m.start(), m.end()):
                    continue
                if max_cases < 1:
                    max_cases = 1

        for pat in death_patterns:
            for m in re.finditer(pat, text, re.I):
                if has_historical_context(text, m.start(), m.end()):
                    continue
                try:
                    n = int(m.group(1))
                    if 0 < n <= 1000 and n > max_deaths:
                        max_deaths = n
                except (ValueError, IndexError):
                    continue

        for pat in first_death_patterns:
            for m in re.finditer(pat, text, re.I):
                if has_historical_context(text, m.start(), m.end()):
                    continue
                if max_deaths < 1:
                    max_deaths = 1

        if max_cases > 0 or max_deaths > 0:
            results[code] = {"cases": max_cases, "deaths": max_deaths}

    return results


def fetch_all_entries() -> tuple[list[dict], list[dict]]:
    """Fetches all RSS sources. Returns (news_items, raw_entries)."""
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    news_items: list[dict] = []
    raw_entries: list[dict] = []

    for name, url in RSS_SOURCES:
        try:
            feed = feedparser.parse(url, request_headers=HEADERS)
            added = 0
            for entry in (feed.entries or [])[:30]:
                title   = (entry.get("title") or "").strip()
                summary = clean(entry.get("summary", ""))
                link    = entry.get("link", "")

                # Calcular fecha de publicación cuanto antes para incluirla en raw_entries
                try:
                    pub_struct = entry.get("published_parsed") or entry.get("updated_parsed")
                    pub = time.strftime("%Y-%m-%d", pub_struct) if pub_struct else (entry.get("published", "") or "")[:10]
                except Exception:
                    pub = (entry.get("published", "") or "")[:10]

                raw_entries.append({"title": title, "summary": summary, "source": name, "date": pub})

                if not link or link in seen_urls:
                    continue
                if not any(kw in (title + " " + summary).lower() for kw in KEYWORDS):
                    continue

                title_key = re.sub(r"\s+", " ", title.lower())[:80]
                if title_key in seen_titles:
                    continue
                seen_urls.add(link)
                seen_titles.add(title_key)

                # Para feeds de Google News (filtros site:), el title del feed es
                # genérico ("hantavirus - Google News") y no diferencia fuentes,
                # así que usamos el nombre local definido en RSS_SOURCES.
                if "news.google.com" in url:
                    source_label = name
                else:
                    source_label = clean(feed.feed.get("title", "")) or name

                news_items.append({
                    "title":  title,
                    "url":    link,
                    "date":   pub or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "source": source_label,
                    "desc":   summary[:240],
                })
                added += 1
            print(f"  [{name}] +{added} noticias relevantes")
        except Exception as exc:
            print(f"  [{name}] ERROR: {exc}")
        time.sleep(1.5)

    news_items.sort(key=lambda x: x["date"], reverse=True)
    return news_items[:18], raw_entries


def try_update_metrics(data: dict, raw_entries: list[dict]) -> bool:
    """Detecta totales globales y atribución por país con patrones estrictos."""
    current = data["current"]
    best_cases     = current["cases"]
    best_deaths    = current["deaths"]
    best_recovered = current.get("recovered", 0)
    per_country: dict[str, dict] = {}

    # El brote empezó el día 0 (data.metadata.day0). Solo procesamos artículos
    # cuya fecha de publicación sea >= ese día. Esto descarta automáticamente
    # noticias antiguas sobre hantavirus endémico en Argentina/Chile.
    day0_str = data.get("metadata", {}).get("day0", "2026-04-01")

    for entry in raw_entries:
        # Filtro por fecha: descartar artículos publicados antes del día 0
        entry_date = entry.get("date", "")
        if entry_date and len(entry_date) >= 10 and entry_date[:10] < day0_str:
            continue

        text = (entry["title"] + " " + entry["summary"]).lower()
        if not any(kw in text for kw in KEYWORDS):
            continue
        if not is_current_outbreak_article(text):
            continue

        cases     = extract_first_number(text, CASE_PATTERNS,     1, 10000)
        deaths    = extract_first_number(text, DEATH_PATTERNS,    0, 1000)
        recovered = extract_first_number(text, RECOVERED_PATTERNS,0, 10000)

        if cases     and cases     > best_cases:     best_cases     = cases
        if deaths    and deaths    > best_deaths:    best_deaths    = deaths
        if recovered and recovered > best_recovered: best_recovered = recovered

        # Atribución por país con patrones estrictos (sujeto-verbo / "en X país")
        country_metrics = extract_country_metrics(text)
        for code, vals in country_metrics.items():
            best = per_country.setdefault(code, {"cases": 0, "deaths": 0})
            if vals["cases"]  > best["cases"]:  best["cases"]  = vals["cases"]
            if vals["deaths"] > best["deaths"]: best["deaths"] = vals["deaths"]

    changed = False
    changes_log: list[str] = []

    # Globales
    if best_cases > current["cases"]:
        changes_log.append(f"casos {current['cases']}→{best_cases}")
        current["cases"] = best_cases
        changed = True
    if best_deaths > current["deaths"]:
        changes_log.append(f"muertes {current['deaths']}→{best_deaths}")
        current["deaths"] = best_deaths
        changed = True
    if best_recovered > current.get("recovered", 0):
        changes_log.append(f"recuperados {current.get('recovered', 0)}→{best_recovered}")
        current["recovered"] = best_recovered
        changed = True

    countries_arr = data.setdefault("countries", [])

    # Aplicar cambios por país (actualizar existentes, auto-añadir nuevos)
    for code, vals in per_country.items():
        existing = next((c for c in countries_arr if c["code"] == code), None)
        if existing:
            if vals["cases"] > existing.get("cases", 0):
                changes_log.append(f"{code}: casos {existing.get('cases', 0)}→{vals['cases']}")
                existing["cases"] = vals["cases"]
                changed = True
            if vals["deaths"] > existing.get("deaths", 0):
                changes_log.append(f"{code}: muertes {existing.get('deaths', 0)}→{vals['deaths']}")
                existing["deaths"] = vals["deaths"]
                changed = True
        elif vals["cases"] > 0:
            # País nuevo detectado vía patrón estricto — auto-añadir
            meta = COUNTRY_DATA.get(code, {})
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            new_country = {
                "code":       code,
                "name":       meta.get("name", code),
                "iso_num":    meta.get("iso_num", ""),
                "cases":      vals["cases"],
                "deaths":     vals["deaths"],
                "status":     "Detectado automáticamente vía noticias",
                "first_case": today,
                "notes":      "Caso detectado por el scraper automático. Pendiente de revisión manual para añadir contexto epidemiológico.",
            }
            countries_arr.append(new_country)
            changes_log.append(f"NUEVO PAÍS {code}: cases={vals['cases']} deaths={vals['deaths']}")
            changed = True

    # Total global no puede ser menor que la suma de países
    sum_country_cases  = sum(c.get("cases", 0)  for c in countries_arr)
    sum_country_deaths = sum(c.get("deaths", 0) for c in countries_arr)
    if sum_country_cases > current["cases"]:
        current["cases"] = sum_country_cases
        changed = True
    if sum_country_deaths > current["deaths"]:
        current["deaths"] = sum_country_deaths
        changed = True

    countries_with_cases = len([c for c in countries_arr if c.get("cases", 0) > 0])
    if countries_with_cases > current.get("countries", 0):
        current["countries"] = countries_with_cases
        changed = True

    if changes_log:
        print(f"  CAMBIOS: {' | '.join(changes_log)}")
    else:
        print(f"  Sin cambios (casos: {current['cases']}, muertes: {current['deaths']}, recuperados: {current.get('recovered', 0)})")

    # Auto-extender timeline
    if changed:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        timeline = data.setdefault("timeline", [])
        last = timeline[-1] if timeline else None

        snapshot = {
            c["code"]: {"cases": c["cases"], "deaths": c["deaths"]}
            for c in countries_arr if c.get("cases", 0) > 0
        }
        event_text = (
            f"Actualización: {current['cases']} casos · "
            f"{current['deaths']} fallecidos · "
            f"{current.get('recovered', 0)} recuperados"
        )

        if last and last["date"] == today:
            last["cases"]     = current["cases"]
            last["deaths"]    = current["deaths"]
            last["recovered"] = current.get("recovered", 0)
            last["snapshot"]  = snapshot
            last["event"]     = event_text
        else:
            timeline.append({
                "date":      today,
                "cases":     current["cases"],
                "deaths":    current["deaths"],
                "recovered": current.get("recovered", 0),
                "event":     event_text,
                "snapshot":  snapshot,
            })

    return changed


def main():
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))

    news, raw_entries = fetch_all_entries()

    if try_update_metrics(data, raw_entries):
        print("  ✓ Métricas y timeline actualizados")

    if news:
        data["news"] = news
        print(f"  ✓ Noticias guardadas: {len(news)}")
    else:
        print("  ⚠ Sin noticias nuevas — se mantienen las anteriores")

    data["metadata"]["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"  ✓ data.json actualizado — {data['metadata']['updated']}")


if __name__ == "__main__":
    main()
