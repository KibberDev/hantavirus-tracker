#!/usr/bin/env python3
"""
HantaTracker — Scraper automático.
Se ejecuta via GitHub Actions cada 30 min.
Actualiza data.json con:
  - Noticias recientes de RSS feeds (filtradas por hantavirus)
  - Extracción de casos desde TODAS las fuentes, no solo WHO
  - Timestamp de última actualización
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
    r"(\d+)\s+(?:laboratory[\-\s]?confirmed\s+)?(?:human\s+)?cases?",
    r"total\s+(?:of\s+)?(\d+)\s+cases?",
    r"(\d+)\s+persons?\s+(?:infected|affected|confirmed)",
    r"confirmed[:\s]+(\d+)",
]

DEATH_PATTERNS = [
    r"(\d+)\s+(?:deaths?|fatalities?|fatal\s+cases?)",
    r"(\d+)\s+(?:persons?\s+)?(?:have\s+)?died",
]


def clean(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def fetch_all_entries() -> tuple[list[dict], list[dict]]:
    """Fetches all RSS sources. Returns (news_items, raw_entries)."""
    seen: set[str] = set()
    news_items: list[dict] = []
    raw_entries: list[dict] = []

    for name, url in RSS_SOURCES:
        try:
            feed = feedparser.parse(url, request_headers=HEADERS)
            added = 0
            for entry in (feed.entries or [])[:30]:
                title   = entry.get("title", "")
                summary = clean(entry.get("summary", ""))
                link    = entry.get("link", "")

                raw_entries.append({"title": title, "summary": summary})

                if not link or link in seen:
                    continue
                if not any(kw in (title + summary).lower() for kw in KEYWORDS):
                    continue
                seen.add(link)
                pub = (entry.get("published") or entry.get("updated") or "")[:10]
                news_items.append({
                    "title":  title.strip(),
                    "url":    link,
                    "date":   pub or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "source": clean(feed.feed.get("title", name)),
                    "desc":   summary[:220],
                })
                added += 1
            print(f"  [{name}] +{added} noticias relevantes")
        except Exception as exc:
            print(f"  [{name}] ERROR: {exc}")
        time.sleep(1.5)

    news_items.sort(key=lambda x: x["date"], reverse=True)
    return news_items[:15], raw_entries


def try_update_cases(data: dict, raw_entries: list[dict]) -> bool:
    """Scan ALL fetched entries for updated case counts."""
    current_cases  = data["current"]["cases"]
    current_deaths = data["current"]["deaths"]
    best_n      = current_cases
    best_deaths = current_deaths

    for entry in raw_entries:
        text = (entry["title"] + " " + entry["summary"]).lower()
        if not any(kw in text for kw in KEYWORDS):
            continue

        for pattern in CASE_PATTERNS:
            m = re.search(pattern, text, re.I)
            if m:
                n = int(m.group(1))
                if n > best_n:
                    best_n = n
                    for dp in DEATH_PATTERNS:
                        dm = re.search(dp, text, re.I)
                        if dm:
                            best_deaths = int(dm.group(1))
                break

    if best_n > current_cases:
        print(f"  Casos: {current_cases} → {best_n} | Muertes: {current_deaths} → {best_deaths}")
        data["current"]["cases"]  = best_n
        data["current"]["deaths"] = best_deaths
        countries = data.get("countries", [])
        data["current"]["countries"] = len([c for c in countries if c.get("cases", 0) > 0])
        return True

    print(f"  Sin nuevos casos confirmados (actual: {current_cases})")
    return False


def main():
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))

    news, raw_entries = fetch_all_entries()

    if try_update_cases(data, raw_entries):
        print("  Casos actualizados ✓")

    if news:
        data["news"] = news
        print(f"  Noticias guardadas: {len(news)}")
    else:
        print("  Sin noticias nuevas — se mantienen las anteriores")

    data["metadata"]["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"  ✓ data.json actualizado — {data['metadata']['updated']}")


if __name__ == "__main__":
    main()
