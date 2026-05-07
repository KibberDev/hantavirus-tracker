#!/usr/bin/env python3
"""
HantaTracker — Scraper automático.
Se ejecuta via GitHub Actions cada 30 min.
Actualiza data.json con:
  - Noticias recientes de RSS feeds (filtradas por hantavirus)
  - Intento de extracción de casos desde WHO DON
  - Timestamp de última actualización
"""
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests

ROOT      = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data.json"

RSS_SOURCES = [
    ("Google News ES", "https://news.google.com/rss/search?q=hantavirus&hl=es&gl=ES&ceid=ES:es"),
    ("Google News EN", "https://news.google.com/rss/search?q=hantavirus&hl=en&gl=US&ceid=US:en"),
    ("WHO DON",        "https://www.who.int/feeds/entity/don/en/rss.xml"),
    ("BBC Health",     "https://feeds.bbci.co.uk/news/health/rss.xml"),
    ("Reuters Health", "https://feeds.reuters.com/reuters/healthNews"),
]

KEYWORDS = ["hanta", "mv hondius", "cepa andes", "andes strain"]


def clean(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def fetch_news() -> list[dict]:
    seen: set[str] = set()
    items: list[dict] = []

    for name, url in RSS_SOURCES:
        try:
            feed = feedparser.parse(url)
            added = 0
            for entry in (feed.entries or [])[:30]:
                title   = entry.get("title", "")
                summary = clean(entry.get("summary", ""))
                link    = entry.get("link", "")
                if not link or link in seen:
                    continue
                if not any(kw in (title + summary).lower() for kw in KEYWORDS):
                    continue
                seen.add(link)
                pub = (entry.get("published") or entry.get("updated") or "")[:10]
                items.append({
                    "title":  title.strip(),
                    "url":    link,
                    "date":   pub or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "source": clean(feed.feed.get("title", name)),
                    "desc":   summary[:220],
                })
                added += 1
            print(f"  [{name}] +{added} items")
        except Exception as exc:
            print(f"  [{name}] ERROR: {exc}")
        time.sleep(1.5)

    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:15]


def try_update_cases(data: dict) -> bool:
    """Intenta detectar nuevos casos en el RSS de la OMS."""
    try:
        feed = feedparser.parse("https://www.who.int/feeds/entity/don/en/rss.xml")
        for entry in (feed.entries or [])[:10]:
            text = (entry.get("title", "") + " " + clean(entry.get("summary", ""))).lower()
            if not any(kw in text for kw in KEYWORDS):
                continue
            m_cases  = re.search(r"(\d+)\s+(?:laboratory[\-\s]?confirmed\s+)?cases?", text, re.I)
            m_deaths = re.search(r"(\d+)\s+(?:deaths?|fatalities?)", text, re.I)
            if m_cases:
                n = int(m_cases.group(1))
                if n > data["current"]["cases"]:
                    print(f"  WHO: casos {data['current']['cases']} → {n}")
                    data["current"]["cases"] = n
                    if m_deaths:
                        data["current"]["deaths"] = int(m_deaths.group(1))
                    data["current"]["countries"] = len([
                        c for c in data["countries"] if c["cases"] > 0
                    ])
                    return True
    except Exception as exc:
        print(f"  WHO cases: {exc}")
    return False


def main():
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))

    # 1. Intentar actualizar casos desde OMS
    if try_update_cases(data):
        print("  Casos actualizados ✓")
    else:
        print("  Sin cambios en casos")

    # 2. Noticias
    news = fetch_news()
    if news:
        data["news"] = news
        print(f"  Noticias guardadas: {len(news)}")
    else:
        print("  Sin noticias nuevas — se mantienen las anteriores")

    # 3. Timestamp
    data["metadata"]["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"  ✓ data.json actualizado — {data['metadata']['updated']}")


if __name__ == "__main__":
    main()
