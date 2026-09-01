from __future__ import annotations

"""Metadata-only online content providers for proactive bubbles.

The registry defaults to network-disabled and has no implicit HTTP client.  A
caller must both inject a fetcher and pass ``allow_network=True`` for an
explicit refresh, matching the first-use authorization boundary in v0.2.
"""

import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping, Protocol, Sequence

from .companion import BubbleSource, ContentCategory


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


class HttpFetcher(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 15.0,
    ) -> HttpResponse: ...


class UrllibFetcher:
    """Optional synchronous HTTP adapter; never constructed automatically."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 15.0,
    ) -> HttpResponse:
        request = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - explicit opt-in URL
            return HttpResponse(
                int(response.status),
                response.read(),
                {str(key): str(value) for key, value in response.headers.items()},
            )


@dataclass(frozen=True, slots=True)
class ContentItem:
    id: str
    category: ContentCategory
    title: str
    summary: str
    source: str
    published_at: datetime | None
    url: str
    topics: tuple[str, ...] = ()
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(
        cls,
        *,
        category: ContentCategory | str,
        title: str,
        summary: str,
        source: str,
        published_at: datetime | str | None,
        url: str,
        topics: Sequence[str] = (),
        stable_id: str = "",
        fetched_at: datetime | None = None,
    ) -> "ContentItem":
        clean_title = _clean_text(title, 500)
        if not clean_title:
            raise ValueError("content title cannot be empty")
        clean_url = str(url).strip()
        identifier = stable_id.strip() or _stable_id(source, clean_url, clean_title)
        return cls(
            id=identifier[:240],
            category=category if isinstance(category, ContentCategory) else ContentCategory(category),
            title=clean_title,
            summary=_clean_text(summary, 1200),
            source=_clean_text(source, 120),
            published_at=parse_date(published_at),
            url=clean_url[:2048],
            topics=tuple(dict.fromkeys(_clean_text(topic, 80) for topic in topics if _clean_text(topic, 80))),
            fetched_at=_utc(fetched_at or datetime.now(timezone.utc)),
        )

    def stale(self, now: datetime | None = None, maximum_age_days: int = 14) -> bool:
        if self.published_at is None:
            return True
        return _utc(now or datetime.now(timezone.utc)) - self.published_at > timedelta(
            days=max(0, maximum_age_days)
        )

    def source_attribution(self) -> BubbleSource:
        return BubbleSource(self.source, self.url, self.published_at)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category.value,
            "title": self.title,
            "summary": self.summary,
            "source": self.source,
            "publishedAt": self.published_at.isoformat() if self.published_at else "",
            "url": self.url,
            "topics": list(self.topics),
            "fetchedAt": self.fetched_at.isoformat(),
            "stale": self.stale(),
        }


@dataclass(frozen=True, slots=True)
class RefreshResult:
    provider_id: str
    items: tuple[ContentItem, ...]
    state: str
    from_cache: bool
    refreshed_at: datetime | None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "providerId": self.provider_id,
            "items": [item.to_mapping() for item in self.items],
            "state": self.state,
            "fromCache": self.from_cache,
            "refreshedAt": self.refreshed_at.isoformat() if self.refreshed_at else "",
        }


@dataclass(frozen=True, slots=True)
class CacheEntry:
    items: tuple[ContentItem, ...]
    stored_at: datetime


class ContentCache(Protocol):
    def get(self, key: str) -> CacheEntry | None: ...

    def put(self, key: str, value: CacheEntry) -> None: ...


class MemoryContentCache:
    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry] = {}

    def get(self, key: str) -> CacheEntry | None:
        return self._entries.get(key)

    def put(self, key: str, value: CacheEntry) -> None:
        self._entries[key] = value


class ContentProvider(ABC):
    provider_id = ""
    label = ""
    minimum_interval_seconds = 60.0
    cache_ttl_seconds = 30.0 * 60.0

    @abstractmethod
    def fetch(
        self,
        fetcher: HttpFetcher,
        query: str,
        limit: int,
        now: datetime,
    ) -> list[ContentItem]:
        raise NotImplementedError

    @staticmethod
    def _response(fetcher: HttpFetcher, url: str) -> HttpResponse:
        response = fetcher.get(
            url,
            headers={
                "Accept": "application/json, application/atom+xml, application/rss+xml, application/xml;q=0.9",
                "User-Agent": "Lilies-in-the-box/0.2 (metadata-only; user-initiated)",
            },
        )
        if not 200 <= int(response.status) < 300:
            raise RuntimeError(f"content source returned HTTP {response.status}")
        if len(response.body) > 8 * 1024 * 1024:
            raise RuntimeError("content source response exceeded metadata size limit")
        return response


class ArxivProvider(ContentProvider):
    provider_id = "arxiv"
    label = "arXiv"
    minimum_interval_seconds = 3.0
    cache_ttl_seconds = 60.0 * 60.0

    def fetch(self, fetcher: HttpFetcher, query: str, limit: int, now: datetime) -> list[ContentItem]:
        params = urllib.parse.urlencode(
            {
                "search_query": f"all:{query.strip() or 'artificial intelligence'}",
                "start": 0,
                "max_results": limit,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
        )
        response = self._response(fetcher, f"https://export.arxiv.org/api/query?{params}")
        root = ET.fromstring(response.body)
        items: list[ContentItem] = []
        for entry in root.findall("{*}entry")[:limit]:
            title = _xml_text(entry, "{*}title")
            identifier = _xml_text(entry, "{*}id")
            link = identifier
            for candidate in entry.findall("{*}link"):
                if candidate.attrib.get("rel") == "alternate" and candidate.attrib.get("href"):
                    link = candidate.attrib["href"]
                    break
            categories = [node.attrib.get("term", "") for node in entry.findall("{*}category")]
            items.append(
                ContentItem.create(
                    category=ContentCategory.RESEARCH,
                    title=title,
                    summary=_xml_text(entry, "{*}summary"),
                    source=self.label,
                    published_at=_xml_text(entry, "{*}published"),
                    url=link,
                    topics=categories,
                    stable_id=identifier,
                    fetched_at=now,
                )
            )
        return items


class CrossrefProvider(ContentProvider):
    provider_id = "crossref"
    label = "Crossref"
    minimum_interval_seconds = 1.0
    cache_ttl_seconds = 60.0 * 60.0

    def fetch(self, fetcher: HttpFetcher, query: str, limit: int, now: datetime) -> list[ContentItem]:
        params = urllib.parse.urlencode(
            {
                "query": query.strip() or "science",
                "rows": limit,
                "sort": "published",
                "order": "desc",
                "select": "DOI,title,abstract,published,URL,container-title,subject",
            }
        )
        payload = _json(self._response(fetcher, f"https://api.crossref.org/works?{params}"))
        records = payload.get("message", {}).get("items", [])
        items: list[ContentItem] = []
        for record in records[:limit]:
            if not isinstance(record, Mapping):
                continue
            title = _first(record.get("title"))
            doi = str(record.get("DOI", ""))
            published = _crossref_date(record.get("published"))
            containers = record.get("container-title", [])
            source = _first(containers) or self.label
            url = str(record.get("URL") or (f"https://doi.org/{doi}" if doi else ""))
            try:
                items.append(
                    ContentItem.create(
                        category=ContentCategory.RESEARCH,
                        title=title,
                        summary=str(record.get("abstract", "")),
                        source=source,
                        published_at=published,
                        url=url,
                        topics=[str(value) for value in record.get("subject", [])],
                        stable_id=f"doi:{doi}" if doi else "",
                        fetched_at=now,
                    )
                )
            except ValueError:
                continue
        return items


class PubMedProvider(ContentProvider):
    provider_id = "pubmed"
    label = "PubMed"
    # One refresh performs ESearch + ESummary.  A one-second refresh interval
    # keeps the unauthenticated aggregate comfortably under NCBI's request cap.
    minimum_interval_seconds = 1.0
    cache_ttl_seconds = 60.0 * 60.0

    def fetch(self, fetcher: HttpFetcher, query: str, limit: int, now: datetime) -> list[ContentItem]:
        search_params = urllib.parse.urlencode(
            {
                "db": "pubmed",
                "retmode": "json",
                "retmax": limit,
                "sort": "pub date",
                "term": query.strip() or "science",
            }
        )
        search = _json(
            self._response(
                fetcher,
                f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{search_params}",
            )
        )
        identifiers = [str(value) for value in search.get("esearchresult", {}).get("idlist", [])]
        if not identifiers:
            return []
        summary_params = urllib.parse.urlencode(
            {"db": "pubmed", "retmode": "json", "id": ",".join(identifiers)}
        )
        payload = _json(
            self._response(
                fetcher,
                f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?{summary_params}",
            )
        )
        result = payload.get("result", {})
        items: list[ContentItem] = []
        for identifier in identifiers[:limit]:
            record = result.get(identifier, {})
            if not isinstance(record, Mapping):
                continue
            source = str(record.get("fulljournalname") or record.get("source") or self.label)
            authors = [str(author.get("name", "")) for author in record.get("authors", []) if isinstance(author, Mapping)]
            try:
                items.append(
                    ContentItem.create(
                        category=ContentCategory.RESEARCH,
                        title=str(record.get("title", "")),
                        summary=("; ".join(authors[:4]) if authors else ""),
                        source=source,
                        published_at=str(record.get("pubdate", "")),
                        url=f"https://pubmed.ncbi.nlm.nih.gov/{identifier}/",
                        topics=("biomedicine", "medical research"),
                        stable_id=f"pmid:{identifier}",
                        fetched_at=now,
                    )
                )
            except ValueError:
                continue
        return items


class RssAtomProvider(ContentProvider):
    minimum_interval_seconds = 60.0
    cache_ttl_seconds = 30.0 * 60.0

    def __init__(
        self,
        provider_id: str,
        label: str,
        url: str,
        *,
        category: ContentCategory = ContentCategory.NEWS,
        topics: Sequence[str] = (),
    ) -> None:
        self.provider_id = str(provider_id)
        self.label = str(label)
        self.url = str(url)
        self.category = category
        self.topics = tuple(topics)

    def fetch(self, fetcher: HttpFetcher, query: str, limit: int, now: datetime) -> list[ContentItem]:
        del query
        root = ET.fromstring(self._response(fetcher, self.url).body)
        nodes = root.findall(".//item")
        if not nodes:
            nodes = root.findall("{*}entry")
        items: list[ContentItem] = []
        for node in nodes[:limit]:
            title = _xml_local_text(node, "title")
            link = _xml_local_text(node, "link")
            if not link:
                link_node = _xml_local_node(node, "link")
                if link_node is not None:
                    link = str(link_node.attrib.get("href", ""))
            identifier = _xml_local_text(node, "guid") or _xml_local_text(node, "id") or link
            published = (
                _xml_local_text(node, "pubDate")
                or _xml_local_text(node, "published")
                or _xml_local_text(node, "updated")
            )
            summary = (
                _xml_local_text(node, "description")
                or _xml_local_text(node, "summary")
                or _xml_local_text(node, "content")
            )
            try:
                items.append(
                    ContentItem.create(
                        category=self.category,
                        title=title,
                        summary=summary,
                        source=self.label,
                        published_at=published,
                        url=link,
                        topics=self.topics,
                        stable_id=identifier,
                        fetched_at=now,
                    )
                )
            except ValueError:
                continue
        return items


class GdeltProvider(ContentProvider):
    provider_id = "gdelt"
    label = "GDELT"
    minimum_interval_seconds = 5.0
    cache_ttl_seconds = 15.0 * 60.0

    def fetch(self, fetcher: HttpFetcher, query: str, limit: int, now: datetime) -> list[ContentItem]:
        params = urllib.parse.urlencode(
            {
                "query": query.strip() or "science",
                "mode": "ArtList",
                "maxrecords": limit,
                "format": "json",
                "sort": "DateDesc",
            }
        )
        payload = _json(
            self._response(fetcher, f"https://api.gdeltproject.org/api/v2/doc/doc?{params}")
        )
        items: list[ContentItem] = []
        for record in payload.get("articles", [])[:limit]:
            if not isinstance(record, Mapping):
                continue
            source = str(record.get("domain") or record.get("sourcecountry") or self.label)
            try:
                items.append(
                    ContentItem.create(
                        category=ContentCategory.NEWS,
                        title=str(record.get("title", "")),
                        summary=str(record.get("seendate", "")),
                        source=source,
                        published_at=str(record.get("seendate", "")),
                        url=str(record.get("url", "")),
                        topics=(query.strip(),) if query.strip() else (),
                        stable_id=str(record.get("url", "")),
                        fetched_at=now,
                    )
                )
            except ValueError:
                continue
        return items


def default_providers() -> list[ContentProvider]:
    return [
        ArxivProvider(),
        CrossrefProvider(),
        PubMedProvider(),
        RssAtomProvider(
            "nasa",
            "NASA",
            "https://www.nasa.gov/news-release/feed/",
            topics=("space", "astronomy", "NASA"),
        ),
        RssAtomProvider(
            "jpl",
            "NASA JPL",
            "https://www.jpl.nasa.gov/feeds/news/",
            topics=("space", "planetary science", "JPL"),
        ),
        GdeltProvider(),
    ]


class ContentService:
    """Cache/rate-limit coordinator with a deny-by-default network switch."""

    def __init__(
        self,
        providers: Sequence[ContentProvider] | None = None,
        *,
        fetcher: HttpFetcher | None = None,
        cache: ContentCache | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        current = list(providers) if providers is not None else default_providers()
        self.providers = {provider.provider_id: provider for provider in current}
        self.fetcher = fetcher
        self.cache = cache or MemoryContentCache()
        self.monotonic = monotonic
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._last_request: dict[str, float] = {}

    def sources(self) -> list[dict[str, Any]]:
        return [
            {
                "id": provider.provider_id,
                "label": provider.label,
                "networkReady": self.fetcher is not None,
                "minimumIntervalSeconds": provider.minimum_interval_seconds,
                "cacheTtlSeconds": provider.cache_ttl_seconds,
            }
            for provider in self.providers.values()
        ]

    def refresh(
        self,
        provider_id: str,
        query: str = "",
        *,
        limit: int = 10,
        allow_network: bool = False,
        force: bool = False,
    ) -> RefreshResult:
        try:
            provider = self.providers[str(provider_id)]
        except KeyError as exc:
            raise KeyError(f"unknown content source: {provider_id}") from exc
        maximum = max(1, min(int(limit), 50))
        key = _cache_key(provider.provider_id, query, maximum)
        entry = self.cache.get(key)
        current = _utc(self.now())
        if entry and not force:
            age = (current - entry.stored_at).total_seconds()
            if age < provider.cache_ttl_seconds:
                return RefreshResult(provider.provider_id, entry.items, "fresh-cache", True, entry.stored_at)
        if not allow_network:
            return RefreshResult(
                provider.provider_id,
                entry.items if entry else (),
                "network-disabled",
                bool(entry),
                entry.stored_at if entry else None,
            )
        if self.fetcher is None:
            return RefreshResult(
                provider.provider_id,
                entry.items if entry else (),
                "no-fetcher",
                bool(entry),
                entry.stored_at if entry else None,
            )
        elapsed = self.monotonic() - self._last_request.get(provider.provider_id, -float("inf"))
        if elapsed < provider.minimum_interval_seconds:
            return RefreshResult(
                provider.provider_id,
                entry.items if entry else (),
                "rate-limited",
                bool(entry),
                entry.stored_at if entry else None,
            )
        self._last_request[provider.provider_id] = self.monotonic()
        try:
            items = tuple(provider.fetch(self.fetcher, str(query), maximum, current))
        except Exception:
            if entry:
                return RefreshResult(provider.provider_id, entry.items, "offline-cache", True, entry.stored_at)
            raise
        fresh = CacheEntry(items, current)
        self.cache.put(key, fresh)
        return RefreshResult(provider.provider_id, items, "refreshed", False, current)


def parse_date(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    text = str(value or "").strip()
    if not text:
        return None
    compact = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z", text)
    if compact:
        return datetime(*(int(group) for group in compact.groups()), tzinfo=timezone.utc)
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return _utc(datetime.fromisoformat(candidate))
        except ValueError:
            pass
    try:
        return _utc(parsedate_to_datetime(text))
    except (TypeError, ValueError, OverflowError):
        pass
    year = re.search(r"\b(19|20)\d{2}\b", text)
    if year:
        return datetime(int(year.group()), 1, 1, tzinfo=timezone.utc)
    return None


def _crossref_date(value: Any) -> datetime | None:
    if not isinstance(value, Mapping):
        return None
    parts = value.get("date-parts", [])
    if not parts or not isinstance(parts[0], Sequence):
        return None
    numbers = [int(number) for number in parts[0]][:3]
    while len(numbers) < 3:
        numbers.append(1)
    try:
        return datetime(numbers[0], numbers[1], numbers[2], tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None


def _json(response: HttpResponse) -> dict[str, Any]:
    value = json.loads(response.body.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("content source did not return an object")
    return value


def _xml_text(node: ET.Element, path: str) -> str:
    child = node.find(path)
    return "" if child is None else "".join(child.itertext()).strip()


def _xml_local_node(node: ET.Element, name: str) -> ET.Element | None:
    for child in node:
        if child.tag.rsplit("}", 1)[-1] == name:
            return child
    return None


def _xml_local_text(node: ET.Element, name: str) -> str:
    child = _xml_local_node(node, name)
    return "" if child is None else "".join(child.itertext()).strip()


def _clean_text(value: Any, limit: int) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _first(value: Any) -> str:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return str(value[0]) if value else ""
    return str(value or "")


def _stable_id(source: str, url: str, title: str) -> str:
    payload = f"{source}\0{url}\0{title}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def _cache_key(provider_id: str, query: str, limit: int) -> str:
    value = f"{provider_id}\0{query.strip().casefold()}\0{limit}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
