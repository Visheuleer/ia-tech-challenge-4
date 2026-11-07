from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import Engine

from clinical_monitor.api.app import create_app
from clinical_monitor.application.ingestion import IngestionService
from clinical_monitor.domain.evidence import Evidence
from clinical_monitor.domain.patients import Encounter, Patient
from clinical_monitor.infrastructure.database.engine import create_database_engine
from clinical_monitor.infrastructure.database.service import create_ingestion_service
from clinical_monitor.infrastructure.memory import InMemoryRepository


@pytest.fixture(params=["memory", "sqlite"])
def app_factory(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Callable[[], FastAPI]]:
    engines: list[Engine] = []

    def factory() -> FastAPI:
        if request.param == "memory":
            service = IngestionService(
                InMemoryRepository[Patient](),
                InMemoryRepository[Encounter](),
                InMemoryRepository[Evidence](),
            )
        else:
            engine = create_database_engine(f"sqlite:///{tmp_path / f'{uuid4()}.db'}")
            engines.append(engine)
            config = Config("alembic.ini")
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            service = create_ingestion_service(engine)
        return create_app(ingestion_service=service)

    yield factory
    for engine in engines:
        engine.dispose()
