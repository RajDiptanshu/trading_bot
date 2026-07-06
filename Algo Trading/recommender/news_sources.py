"""
news_sources.py — pluggable news fetch layer for Agent 6 (the pre-market News Brain).

Design goal (user decision 2026-06-20): "free now, design for paid later". Every
source implements the same `NewsSource` ABC, so a paid provider (NewsAPI, Marketaux,
Tickertape…) can be dropped in later by subclassing it — zero change to the brain.

Free sources implemented here (all best-effort, failures NEVER block the pipeline):
  RSSSource          — generic RSS/Atom (Economic Times, Livemint; any feed URL)
  GDELTSource        — GDELT DOC 2.0 global macro/company news (rate-limited; tolerant)
  YFinanceNewsSource — per-symbol yfinance .news (last-24h company headlines)
  DDGSource          — DuckDuckGo text search (per query, best-effort)

Hard rule learned during the build: ALWAYS filter by published-time >= `since`.
Some public feeds (e.g. Moneycontrol) serve stale cached content; the time filter
makes a stale feed contribute nothing instead of polluting the briefing.

No heavy deps: stdlib urllib + xml.etree + email.utils. yfinance/ddgs are already
in requirements and imported lazily so this module loads even if they are missing.
"""
from __future__ import annotations

import html
import re
import ssl
import time
import urllib.request
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

_SSL = ssl.create_default_context()
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NewsBrain/1.0"


# ───────────────────────────── data model ─────────────────────────────
@dataclass
class NewsItem:
    """One headline. `published` is tz-aware UTC (or None if unparseable)."""
    source: str
    title: str
    published: datetime | None = None
    link: str = ""
    summary: str = ""
    symbol: str | None = None          # set by per-symbol sources (yfinance/DDG)
    category: str = "general"          # markets | company | global | macro | results …

    def age_hours(self, now: datetime | None = None) -> float | None:
        if self.published is None:
            return None
        now = now or datetime.now(timezone.utc)
        return round((now - self.published).total_seconds() / 3600, 1)

    def as_dict(self) -> dict:
        return {"source": self.source, "title": self.title, "category": self.category,
                "symbol": self.symbol, "link": self.link,
                "published": self.published.isoformat() if self.published else None,
                "age_h": self.age_hours()}


# ───────────────────────────── http + parse helpers ─────────────────────────────
def _http_get(url: str, timeout: int = 12, retries: int = 2) -> bytes:
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                                       "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
                return r.read()
        except Exception as e:                       # noqa: BLE001 — best effort
            last = e
            if attempt < retries:
                time.sleep(1.2 * (attempt + 1))      # back off (helps GDELT 429s)
    raise last if last else RuntimeError("http_get failed")


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:                                             # ISO 8601 (yfinance content.pubDate etc.)
        if "T" in raw and raw[:4].isdigit():
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        dt = parsedate_to_datetime(raw)              # RFC-822 (RSS standard)
    except Exception:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except Exception:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)             # strip any inline HTML
    return re.sub(r"\s+", " ", text).strip()


def _parse_rss(data: bytes) -> list[dict]:
    """Return [{title, link, summary, pubDate}] from RSS or Atom bytes, tolerant
    of declared-encoding mismatches (some Indian feeds mis-declare UTF-8)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        try:
            root = ET.fromstring(data.decode("utf-8", "replace"))
        except Exception:
            return []
    out: list[dict] = []
    # RSS 2.0: .//item ; Atom: .//{ns}entry
    items = root.findall(".//item")
    if items:
        for it in items:
            out.append({"title": it.findtext("title"),
                        "link": it.findtext("link"),
                        "summary": it.findtext("description"),
                        "pubDate": it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date")})
        return out
    for e in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        link_el = e.find("{http://www.w3.org/2005/Atom}link")
        out.append({"title": e.findtext("{http://www.w3.org/2005/Atom}title"),
                    "link": link_el.get("href") if link_el is not None else "",
                    "summary": e.findtext("{http://www.w3.org/2005/Atom}summary"),
                    "pubDate": e.findtext("{http://www.w3.org/2005/Atom}updated")
                               or e.findtext("{http://www.w3.org/2005/Atom}published")})
    return out


# ───────────────────────────── source ABC ─────────────────────────────
class NewsSource(ABC):
    """Implement this to add a source (free or paid). `fetch` must be best-effort:
    return [] on any failure, never raise into the aggregator."""
    name: str = "source"
    category: str = "general"

    @abstractmethod
    def fetch(self, since: datetime) -> list[NewsItem]:
        ...


class RSSSource(NewsSource):
    def __init__(self, name: str, url: str, category: str = "markets"):
        self.name, self.url, self.category = name, url, category

    def fetch(self, since: datetime) -> list[NewsItem]:
        try:
            rows = _parse_rss(_http_get(self.url))
        except Exception:
            return []
        out = []
        for r in rows:
            title = _clean(r.get("title"))
            if not title:
                continue
            pub = _parse_date(r.get("pubDate"))
            if pub is not None and pub < since:        # stale-feed guard
                continue
            out.append(NewsItem(self.name, title, pub, (r.get("link") or "").strip(),
                                _clean(r.get("summary"))[:300], category=self.category))
        return out


class GDELTSource(NewsSource):
    """GDELT DOC 2.0 — global news for macro/company bellwethers (US futures cues,
    Accenture/Micron-type prints). Rate-limits hard (429); tolerated as best-effort."""
    name = "gdelt"
    category = "global"

    def __init__(self, queries: list[str], max_records: int = 8):
        self.queries, self.max_records = queries, max_records

    def fetch(self, since: datetime) -> list[NewsItem]:
        import json
        out = []
        for q in self.queries:
            url = ("https://api.gdeltproject.org/api/v2/doc/doc?query="
                   + urllib.request.quote(q)
                   + f"&mode=artlist&maxrecords={self.max_records}&format=json&timespan=24h&sort=datedesc")
            try:
                data = _http_get(url, timeout=15, retries=1)
                arts = json.loads(data).get("articles", [])
            except Exception:
                continue                                # 429 / blocked → skip this query
            for a in arts:
                title = _clean(a.get("title"))
                if not title:
                    continue
                pub = _parse_date(a.get("seendate")) or _parse_date(a.get("datetime"))
                out.append(NewsItem("gdelt", title, pub, a.get("url", ""),
                                    a.get("domain", ""), category="global"))
            time.sleep(0.6)                             # be polite to GDELT
        return out


class YFinanceNewsSource(NewsSource):
    """Per-symbol company headlines via yfinance .news (already a project dep)."""
    name = "yfinance"
    category = "company"

    def __init__(self, symbols: list[str], suffix: str = ".NS"):
        self.symbols, self.suffix = symbols, suffix

    def fetch(self, since: datetime) -> list[NewsItem]:
        try:
            import yfinance as yf
        except Exception:
            return []

        def one(sym: str) -> list[NewsItem]:
            try:
                items = yf.Ticker(sym + self.suffix).news or []
            except Exception:
                return []
            rows = []
            for it in items[:8]:
                content = it.get("content", it)
                title = _clean(content.get("title") or it.get("title"))
                if not title:
                    continue
                ts = it.get("providerPublishTime")
                pub = (datetime.fromtimestamp(ts, timezone.utc) if ts else
                       _parse_date(content.get("pubDate")) or _parse_date(content.get("displayTime")))
                if pub is None or pub < since:          # require a real, in-window timestamp
                    continue
                prov = (content.get("provider") or {}).get("displayName") or it.get("publisher") or "yahoo"
                rows.append(NewsItem(f"yf:{prov}", title, pub, category="company", symbol=sym))
            return rows

        out: list[NewsItem] = []
        with ThreadPoolExecutor(max_workers=12) as ex:      # 55 tickers → ~5x faster
            for rows in ex.map(one, self.symbols):
                out.extend(rows)
        return out


class DDGSource(NewsSource):
    """DuckDuckGo search — flexible free fallback for queries/tickers without a feed."""
    name = "ddg"
    category = "general"

    def __init__(self, queries: list[str], symbol_map: dict[str, str] | None = None,
                 max_results: int = 3):
        self.queries, self.symbol_map, self.max_results = queries, symbol_map or {}, max_results

    def fetch(self, since: datetime) -> list[NewsItem]:
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS
        except Exception:
            return []
        out = []
        for q in self.queries:
            try:
                results = list(DDGS().text(q, max_results=self.max_results))
            except Exception:
                continue
            for r in results:
                title = _clean(r.get("title"))
                if title:
                    out.append(NewsItem("ddg", title, None, r.get("href", ""),
                                        _clean(r.get("body"))[:200],
                                        symbol=self.symbol_map.get(q), category="general"))
        return out


# ───────────────────────────── aggregator ─────────────────────────────
def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower())


@dataclass
class NewsAggregator:
    sources: list[NewsSource] = field(default_factory=list)

    def collect(self, since: datetime, parallel: bool = True) -> list[NewsItem]:
        """Fetch every source (isolated failures), dedup by normalized title prefix,
        return newest-first. Undated items are kept (rare) and sorted last."""
        gathered: list[NewsItem] = []
        if parallel and len(self.sources) > 1:
            with ThreadPoolExecutor(max_workers=min(8, len(self.sources))) as ex:
                futs = {ex.submit(s.fetch, since): s for s in self.sources}
                for f in as_completed(futs):
                    try:
                        gathered.extend(f.result() or [])
                    except Exception:
                        pass
        else:
            for s in self.sources:
                try:
                    gathered.extend(s.fetch(since) or [])
                except Exception:
                    pass
        seen, deduped = set(), []
        for it in sorted(gathered, key=lambda x: (x.published is not None, x.published or since),
                         reverse=True):
            key = " ".join(_norm(it.title).split()[:11])
            if key and key in seen:
                continue
            seen.add(key)
            deduped.append(it)
        return deduped


# ───────────────────────────── default free wiring ─────────────────────────────
DEFAULT_RSS_FEEDS = [
    ("ET-markets",   "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "markets"),
    ("ET-stocks",    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms", "markets"),
    ("ET-economy",   "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms", "macro"),
    ("Livemint-mkt", "https://www.livemint.com/rss/markets", "markets"),
    ("Livemint-cos", "https://www.livemint.com/rss/companies", "company"),
]

# Global bellwethers whose prints move NSE sectors (drives contagion; see news_map.json).
DEFAULT_GDELT_QUERIES = [
    "Accenture earnings guidance", "Micron OR TSMC semiconductor outlook",
    "US Federal Reserve rate decision", "Brent crude oil price", "Nvidia AI demand",
]


def default_sources(symbols: list[str] | None = None,
                    gdelt: bool = True, ddg: bool = False) -> list[NewsSource]:
    """The free stack. `symbols` enables per-company yfinance headlines.
    ddg off by default (slow/flaky); enable for targeted catch-up queries."""
    srcs: list[NewsSource] = [RSSSource(n, u, c) for n, u, c in DEFAULT_RSS_FEEDS]
    if gdelt:
        srcs.append(GDELTSource(DEFAULT_GDELT_QUERIES))
    if symbols:
        srcs.append(YFinanceNewsSource(symbols))
    if ddg and symbols:
        srcs.append(DDGSource([f"{s} NSE stock news today" for s in symbols[:10]]))
    return srcs


if __name__ == "__main__":            # quick smoke test:  python news_sources.py
    import sys
    if sys.stdout: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    agg = NewsAggregator(default_sources(["TCS", "INFY", "RELIANCE"], gdelt=True))
    items = agg.collect(since)
    print(f"collected {len(items)} unique items in last 24h\n")
    for it in items[:25]:
        age = it.age_hours()
        age_s = f"{age:>5}" if age is not None else "    ?"
        print(f"[{age_s}h] {it.category:8} {it.source[:14]:14} {it.title[:80]}")
    sys.stdout.flush()
