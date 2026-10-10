import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from database import Database
from integrations import UniversityIntegration
from sync_database import persist


@dataclass
class SyncResult:
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    error: str | None = None


class SyncEngine:
    def __init__(self, database: Database, integrations: dict[str, UniversityIntegration]):
        self.database, self.integrations = database, dict(integrations)
        if set(integrations) - {"mock", "mycamu", "blackboard"}:
            raise ValueError("Unknown sync source.")
        self._lock = asyncio.Lock()

    async def _sync_source(self, source: str) -> SyncResult:
        try:
            provider = self.integrations[source]
            async with asyncio.timeout(60):
                records = dict(courses=await provider.get_courses(), assignments=await provider.get_assignments(),
                               events=await provider.get_calendar_events())
                try:
                    records["announcements"] = await provider.get_announcements()
                except NotImplementedError:
                    records["announcements"] = []
                records["content"] = await provider.get_content() if hasattr(provider, "get_content") else []
            for kind, items in records.items():
                seen = {}
                for item in items:
                    if item.source != source:
                        raise ValueError("Source mismatch.")
                    external = item.external_id
                    if external is None:
                        if source != "mock":
                            raise ValueError("External identity required.")
                        external = (item.code, item.campus) if kind == "courses" else item.id
                    if external in seen and seen[external] != item:
                        raise ValueError("Conflicting source duplicates.")
                    seen[external] = item
                records[kind] = list(seen.values())
            counts = await asyncio.to_thread(persist, self.database, source, records, datetime.now(timezone.utc))
            return SyncResult(counts)
        except TimeoutError:
            return SyncResult(error="Source sync timed out; other sources can still complete.")
        except Exception:
            # Provider exception messages may contain credentials or private response bodies.
            return SyncResult(error="Sync failed; check source configuration, permissions and data validity.")

    async def sync_source(self, source: str) -> SyncResult:
        async with self._lock:
            if source not in self.integrations:
                return SyncResult(error="Source is not configured.")
            return await self._sync_source(source)

    async def sync_mock(self) -> SyncResult:
        return await self.sync_source("mock")

    async def sync_mycamu(self) -> SyncResult:
        return await self.sync_source("mycamu")

    async def sync_blackboard(self) -> SyncResult:
        return await self.sync_source("blackboard")

    async def sync_all(self) -> dict[str, SyncResult]:
        async with self._lock:
            # Each source gets its own transaction and error result.
            return {source: await self._sync_source(source) for source in self.integrations}
