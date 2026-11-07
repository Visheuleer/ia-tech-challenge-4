from uuid import UUID

from sqlalchemy import Engine, Table, insert, select
from sqlalchemy.exc import IntegrityError

from clinical_monitor.application.errors import RecordConflict
from clinical_monitor.domain.base import DomainModel


class SqlRepository[T: DomainModel]:
    def __init__(self, engine: Engine, table: Table, model: type[T], id_field: str) -> None:
        self._engine = engine
        self._table = table
        self._model = model
        self._id_field = id_field

    def get(self, record_id: UUID) -> T | None:
        statement = select(self._table.c.payload).where(self._table.c[self._id_field] == record_id)
        with self._engine.connect() as connection:
            payload = connection.execute(statement).scalar_one_or_none()
        return self._model.model_validate(payload) if payload is not None else None

    def add(self, record_id: UUID, record: T) -> bool:
        if getattr(record, self._id_field) != record_id:
            raise ValueError("Record identifier does not match repository key")
        values = {
            column.name: getattr(record, column.name)
            for column in self._table.columns
            if column.name != "payload"
        }
        values["payload"] = record.model_dump(mode="json")
        try:
            with self._engine.begin() as connection:
                connection.execute(insert(self._table).values(**values))
        except IntegrityError as exc:
            existing = self.get(record_id)
            if existing is not None and existing == record:
                return False
            raise RecordConflict("Identifier or relationship conflicts with stored data") from exc
        return True
