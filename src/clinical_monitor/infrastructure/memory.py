from threading import Lock
from uuid import UUID

from clinical_monitor.application.errors import RecordConflict
from clinical_monitor.domain.base import DomainModel


class InMemoryRepository[T: DomainModel]:
    def __init__(self) -> None:
        self._records: dict[UUID, T] = {}
        self._lock = Lock()

    def get(self, record_id: UUID) -> T | None:
        with self._lock:
            return self._records.get(record_id)

    def add(self, record_id: UUID, record: T) -> bool:
        with self._lock:
            existing = self._records.get(record_id)
            if existing is not None:
                if existing != record:
                    raise RecordConflict("Identifier already exists with different content")
                return False
            self._records[record_id] = record
            return True

    def list(self, filters: dict | None = None, limit: int = 1000) -> list[T]:
        with self._lock:
            return [
                record
                for _, record in sorted(self._records.items())
                if all(getattr(record, name) == value for name, value in (filters or {}).items())
            ][:limit]
