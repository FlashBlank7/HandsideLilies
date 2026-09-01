from __future__ import annotations

import json
from datetime import datetime, timezone

from lilies.core.content import (
    ArxivProvider,
    ContentService,
    CrossrefProvider,
    GdeltProvider,
    HttpResponse,
    PubMedProvider,
    RssAtomProvider,
)


NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)


class FakeFetcher:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def get(self, url: str, **_kwargs) -> HttpResponse:
        self.urls.append(url)
        return self.responses.pop(0)


def response(body: str) -> HttpResponse:
    return HttpResponse(200, body.encode("utf-8"), {})


def test_network_is_deny_by_default_even_with_injected_fetcher() -> None:
    fetcher = FakeFetcher([response("<feed />")])
    service = ContentService([ArxivProvider()], fetcher=fetcher, now=lambda: NOW)
    result = service.refresh("arxiv", "robotics")
    assert result.state == "network-disabled"
    assert result.items == ()
    assert fetcher.urls == []


def test_arxiv_adapter_returns_metadata_source_and_date() -> None:
    xml = """<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>http://arxiv.org/abs/2608.00001</id>
      <published>2026-08-27T08:00:00Z</published><title> A small model </title>
      <summary>Only a short abstract.</summary><category term="cs.AI" />
      <link rel="alternate" href="https://arxiv.org/abs/2608.00001" /></entry>
    </feed>"""
    fetcher = FakeFetcher([response(xml)])
    service = ContentService([ArxivProvider()], fetcher=fetcher, now=lambda: NOW, monotonic=lambda: 10.0)
    result = service.refresh("arxiv", "small models", allow_network=True)
    assert result.state == "refreshed"
    item = result.items[0]
    assert item.source == "arXiv"
    assert item.published_at.isoformat().startswith("2026-08-27")
    assert item.url == "https://arxiv.org/abs/2608.00001"
    assert item.to_mapping()["publishedAt"]


def test_crossref_adapter_strips_markup_and_uses_journal_source() -> None:
    payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.1/example",
                    "title": ["New result"],
                    "abstract": "<jats:p>Short abstract</jats:p>",
                    "published": {"date-parts": [[2026, 8, 1]]},
                    "URL": "https://doi.org/10.1/example",
                    "container-title": ["Journal A"],
                    "subject": ["Biology"],
                }
            ]
        }
    }
    provider = CrossrefProvider()
    fetcher = FakeFetcher([response(json.dumps(payload))])
    items = provider.fetch(fetcher, "biology", 5, NOW)
    assert items[0].summary == "Short abstract"
    assert items[0].source == "Journal A"
    assert items[0].id == "doi:10.1/example"


def test_pubmed_adapter_uses_search_then_summary_without_article_body() -> None:
    search = {"esearchresult": {"idlist": ["123"]}}
    summary = {
        "result": {
            "uids": ["123"],
            "123": {
                "title": "Clinical result",
                "pubdate": "2026 Aug 20",
                "fulljournalname": "Medical Journal",
                "authors": [{"name": "A. Researcher"}],
            },
        }
    }
    fetcher = FakeFetcher([response(json.dumps(search)), response(json.dumps(summary))])
    items = PubMedProvider().fetch(fetcher, "therapy", 3, NOW)
    assert len(fetcher.urls) == 2
    assert items[0].id == "pmid:123"
    assert items[0].summary == "A. Researcher"
    assert items[0].url.endswith("/123/")


def test_rss_atom_nasa_style_adapter_keeps_only_short_metadata() -> None:
    feed = """<rss><channel><item><guid>nasa-1</guid><title>Mission update</title>
      <description><![CDATA[<p>A compact mission summary.</p>]]></description>
      <pubDate>Thu, 27 Aug 2026 10:00:00 GMT</pubDate>
      <link>https://nasa.example/mission</link></item></channel></rss>"""
    provider = RssAtomProvider("nasa-test", "NASA", "https://nasa.example/feed")
    fetcher = FakeFetcher([response(feed)])
    items = provider.fetch(fetcher, "", 10, NOW)
    assert items[0].title == "Mission update"
    assert items[0].summary == "A compact mission summary."
    assert items[0].source == "NASA"


def test_gdelt_adapter_and_cache_rate_limit_behavior() -> None:
    payload = {
        "articles": [
            {
                "title": "Research agency announces result",
                "url": "https://news.example/result",
                "domain": "news.example",
                "seendate": "20260827T120000Z",
            }
        ]
    }
    tick = [100.0]
    fetcher = FakeFetcher([response(json.dumps(payload))])
    service = ContentService(
        [GdeltProvider()], fetcher=fetcher, now=lambda: NOW, monotonic=lambda: tick[0]
    )
    first = service.refresh("gdelt", "science", allow_network=True)
    assert first.items[0].source == "news.example"
    cached = service.refresh("gdelt", "science", allow_network=True)
    assert cached.state == "fresh-cache"
    limited = service.refresh("gdelt", "science", allow_network=True, force=True)
    assert limited.state == "rate-limited"
    assert len(fetcher.urls) == 1


def test_failed_refresh_falls_back_to_cached_metadata() -> None:
    class Flaky:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, _url: str, **_kwargs) -> HttpResponse:
            self.calls += 1
            if self.calls == 1:
                return response("<rss><channel><item><guid>1</guid><title>Cached</title></item></channel></rss>")
            raise OSError("offline")

    tick = [1.0]
    provider = RssAtomProvider("feed", "Feed", "https://example.test/feed")
    service = ContentService([provider], fetcher=Flaky(), now=lambda: NOW, monotonic=lambda: tick[0])
    first = service.refresh("feed", allow_network=True)
    assert first.state == "refreshed"
    tick[0] = 1000.0
    fallback = service.refresh("feed", allow_network=True, force=True)
    assert fallback.state == "offline-cache"
    assert fallback.items[0].title == "Cached"

