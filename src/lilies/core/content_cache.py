from __future__ import annotations

from .content import CacheEntry, ContentItem, parse_date
from .database import Database


class DatabaseContentCache:
    """Metadata-only content cache stored in the F: SQLite database."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, key: str) -> CacheEntry | None:
        value = self.database.content_cache_get(str(key))
        if not value:
            return None
        stored_at = parse_date(str(value.get("storedAt", "")))
        if stored_at is None:
            return None
        items: list[ContentItem] = []
        for raw in value.get("items", []):
            if not isinstance(raw, dict):
                continue
            try:
                fetched_at = parse_date(str(raw.get("fetchedAt", "")))
                items.append(
                    ContentItem.create(
                        category=str(raw.get("category", "")),
                        title=str(raw.get("title", "")),
                        summary=str(raw.get("summary", "")),
                        source=str(raw.get("source", "")),
                        published_at=str(raw.get("publishedAt", "")),
                        url=str(raw.get("url", "")),
                        topics=raw.get("topics", []) if isinstance(raw.get("topics"), list) else [],
                        stable_id=str(raw.get("id", "")),
                        fetched_at=fetched_at,
                    )
                )
            except (TypeError, ValueError):
                continue
        return CacheEntry(tuple(items), stored_at)

    def put(self, key: str, value: CacheEntry) -> None:
        self.database.content_cache_put(
            str(key),
            [item.to_mapping() for item in value.items],
            value.stored_at.isoformat(),
        )


__all__ = ["DatabaseContentCache"]
