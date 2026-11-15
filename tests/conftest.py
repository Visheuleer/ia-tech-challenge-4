from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
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


@pytest.fixture
def temporal_payload() -> dict:
    patient_id, encounter_id = str(uuid4()), str(uuid4())
    return {
        "series_id": str(uuid4()),
        "measurements": [
            {
                "source_id": str(uuid4()),
                "patient_id": patient_id,
                "encounter_id": encounter_id,
                "signal": "heart_rate",
                "value": 25.0,
                "unit": "bpm",
                "observed_at": (
                    datetime(2020, 1, 1, 12, tzinfo=UTC) + timedelta(seconds=seconds)
                ).isoformat(),
                "quality_status": "valid",
            }
            for seconds in [0, 5, 10]
        ],
    }
