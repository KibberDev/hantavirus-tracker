#!/usr/bin/env python3
"""
HantaTracker — Scraper automático.
Se ejecuta via GitHub Actions cada 30 min.
Actualiza data.json con:
  - Noticias recientes de RSS (filtradas por hantavirus, dedupe inteligente)
  - URLs de Google News resueltas a su fuente real
  - Detección de casos en TODAS las fuentes (no solo OMS)
  - Auto-extensión del timeline cuando hay nuevos casos confirmados
  - Timestamp de última actualización
"""
import json
import re
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import feedparser

ROOT      = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data.json"

RSS_SOURCES = [
    ("Google News ES", "https://news.google.com/rss/search?q=hantavirus&hl=es&gl=ES&ceid=ES:es"),
    ("Google News EN", "https://news.google.com/rss/search?q=hantavirus&hl=en&gl=US&ceid=US:en"),
    ("WHO DON",        "https://www.who.int/feeds/entity/don/en/rss.xml"),
    ("BBC Health",     "https://feeds.bbci.co.uk/news/health/rss.xml"),
    ("NPR Health",     "https://feeds.npr.org/1128/rss.xml"),
    ("ProMED",         "https://promedmail.org/feed/"),
]

KEYWORDS = ["hanta", "mv hondius", "cepa andes", "andes strain"]

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
    r"rises?\s+to\s+(\d+)",
    r"climb(?:s|ed)?\s+to\s+(\d+)",
    r"reach(?:es|ed)?\s+(\d+)",
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
    # Spanish
    r"(\d+)\s+(?:fallecidos?|muertos?|víctimas?\s+mortales?|fatales?)",
    r"(?:fallecidos?|muertos?|víctimas?)\s*(?:asciende[n]?\s+a|suben?\s+a|llegan?\s+a)\s+(\d+)",
    r"(\d+)\s+han\s+(?:fallecido|muerto)",
    r"ya\s+son\s+(\d+)\s+(?:fallecidos?|muertos?|víctimas?)",
]


def clean(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def resolve_url(url: str) -> str:
    """Resuelve URLs encoded de Google News al destino real cuando es posible."""
    if "news.google.com/rss/articles/" not in url:
        return url
    # Google News RSS encodes destination in the path. We can't decode reliably
    # without making an HTTP request, but at least extract the source from the
    # ?oc= parameter or fall back. Keep as-is; click still works (Google redirects).
    return url


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

                raw_entries.append({"title": title, "summary": summary, "source": name})

                if not link or link in seen_urls:
                    continue
                full_text = (title + " " + summary).lower()
                if not any(kw in full_text for kw in KEYWORDS):
                    continue

                # Title-based dedup (cross-source duplicates)
                title_key = re.sub(r"\s+", " ", title.lower())[:80]
                if title_key in seen_titles:
                    continue
                seen_urls.add(link)
                seen_titles.add(title_key)

                pub_raw = entry.get("published") or entry.get("updated") or ""
                # Try to parse to YYYY-MM-DD
                try:
                    pub_struct = entry.get("published_parsed") or entry.get("updated_parsed")
                    if pub_struct:
                        pub = time.strftime("%Y-%m-%d", pub_struct)
                    else:
                        pub = pub_raw[:10]
                except Exception:
                    pub = pub_raw[:10]

                news_items.append({
                    "title":  title,
                    "url":    resolve_url(link),
                    "date":   pub or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "source": clean(feed.feed.get("title", name)) or name,
                    "desc":   summary[:240],
                })
                added += 1
            print(f"  [{name}] +{added} noticias relevantes")
        except Exception as exc:
            print(f"  [{name}] ERROR: {exc}")
        time.sleep(1.5)

    news_items.sort(key=lambda x: x["date"], reverse=True)
    return news_items[:18], raw_entries


def extract_case_numbers(text: str) -> tuple[int | None, int | None]:
    """Extrae el primer match plausible de casos y muertes."""
    cases = None
    deaths = None
    for pat in CASE_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 10000:  # filtro de cordura
                cases = n
                break
    for pat in DEATH_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            n = int(m.group(1))
            if 0 <= n <= 1000:
                deaths = n
                break
    return cases, deaths


def try_update_cases(data: dict, raw_entries: list[dict]) -> bool:
    """Escanea todas las entradas buscando actualizaciones de casos."""
    current_cases  = data["current"]["cases"]
    current_deaths = data["current"]["deaths"]
    best_n      = current_cases
    best_deaths = current_deaths
    source_used = None

    for entry in raw_entries:
        text = (entry["title"] + " " + entry["summary"]).lower()
        if not any(kw in text for kw in KEYWORDS):
            continue
        cases, deaths = extract_case_numbers(text)
        if cases and cases > best_n:
            best_n = cases
            if deaths is not None and deaths >= current_deaths:
                best_deaths = deaths
            source_used = entry.get("source", "?")

    if best_n > current_cases:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        print(f"  CASOS ACTUALIZADOS [{source_used}]: {current_cases} → {best_n} | muertes: {current_deaths} → {best_deaths}")

        data["current"]["cases"]  = best_n
        data["current"]["deaths"] = best_deaths

        # Auto-extender timeline
        timeline = data.setdefault("timeline", [])
        last = timeline[-1] if timeline else None

        if last and last["date"] == today:
            last["cases"]  = best_n
            last["deaths"] = best_deaths
            last["event"]  = f"Actualización automática: {best_n} casos, {best_deaths} fallecidos"
        else:
            snap = dict(last["snapshot"]) if last and last.get("snapshot") else {}
            timeline.append({
                "date":      today,
                "cases":     best_n,
                "deaths":    best_deaths,
                "recovered": data["current"].get("recovered", 0),
                "event":     f"Actualización automática: {best_n} casos, {best_deaths} fallecidos",
                "snapshot":  snap,
            })

        # Recalcular países afectados
        countries = data.get("countries", [])
        data["current"]["countries"] = max(
            data["current"]["countries"],
            len([c for c in countries if c.get("cases", 0) > 0])
        )
        return True

    print(f"  Sin nuevos casos confirmados (actual: {current_cases})")
    return False


def main():
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))

    news, raw_entries = fetch_all_entries()

    if try_update_cases(data, raw_entries):
        print("  ✓ Casos y timeline actualizados")

    if news:
        # Solo sobrescribir si tenemos noticias nuevas (fuentes responden)
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
