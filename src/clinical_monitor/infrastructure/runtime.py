from threading import Lock
from uuid import UUID

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.exc import IntegrityError

from clinical_monitor.infrastructure.database.schema import runtime_records


class SqlRuntimeStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, key: str) -> tuple[int, dict] | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(runtime_records.c.version, runtime_records.c.payload).where(
                    runtime_records.c.record_key == key
                )
            ).first()
        return (row.version, row.payload) if row else None

    def create(self, key: str, kind: str, encounter_id: UUID, payload: dict) -> bool:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(runtime_records).values(
                        record_key=key,
                        kind=kind,
                        encounter_id=encounter_id,
                        version=1,
                        payload=payload,
                    )
                )
            return True
        except IntegrityError:
            if self.get(key) is None:
                raise
            return False

    def replace(self, key: str, version: int, payload: dict) -> bool:
        with self._engine.begin() as connection:
            result = connection.execute(
                update(runtime_records)
                .where(runtime_records.c.record_key == key, runtime_records.c.version == version)
                .values(version=version + 1, payload=payload)
            )
            return result.rowcount == 1

    def list(self, kind: str, encounter_id: UUID | None = None, limit: int = 1000) -> list[dict]:
        statement = select(runtime_records.c.payload).where(runtime_records.c.kind == kind)
        if encounter_id is not None:
            statement = statement.where(runtime_records.c.encounter_id == encounter_id)
        with self._engine.connect() as connection:
            return list(
                connection.execute(
                    statement.order_by(runtime_records.c.record_key).limit(limit)
                ).scalars()
            )


class MemoryRuntimeStore:
    def __init__(self) -> None:
        self._records: dict[str, tuple[str, UUID, int, dict]] = {}
        self._lock = Lock()

    def get(self, key: str) -> tuple[int, dict] | None:
        with self._lock:
            row = self._records.get(key)
            return (row[2], row[3]) if row else None

    def create(self, key: str, kind: str, encounter_id: UUID, payload: dict) -> bool:
        with self._lock:
            if key in self._records:
                return False
            self._records[key] = (kind, encounter_id, 1, payload)
            return True

    def replace(self, key: str, version: int, payload: dict) -> bool:
        with self._lock:
            row = self._records.get(key)
            if row is None or row[2] != version:
                return False
            self._records[key] = (row[0], row[1], version + 1, payload)
            return True

    def list(self, kind: str, encounter_id: UUID | None = None, limit: int = 1000) -> list[dict]:
        with self._lock:
            return [
                row[3]
                for _, row in sorted(self._records.items())
                if row[0] == kind and (encounter_id is None or row[1] == encounter_id)
            ][:limit]
