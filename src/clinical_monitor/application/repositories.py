from typing import Protocol
from uuid import UUID

from clinical_monitor.domain.base import DomainModel


class Repository[T: DomainModel](Protocol):
    def get(self, record_id: UUID) -> T | None: ...

    def add(self, record_id: UUID, record: T) -> bool: ...
