import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from clinical_monitor.api.app import create_app
from clinical_monitor.core.config import Settings
from clinical_monitor.infrastructure.database.engine import create_database_engine
from clinical_monitor.infrastructure.database.schema import encounters, evidence, metadata, patients


@pytest.fixture(params=["sqlite", "postgresql"])
def database(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[tuple[Engine, Config, str]]:
    admin_engine = None
    schema_name = f"test_clinical_{uuid4().hex}"
    url = f"sqlite:///{tmp_path / 'records.db'}"
    if request.param == "postgresql":
        postgres_url = os.environ.get("TEST_DATABASE_URL")
        if not postgres_url:
            pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
        admin_engine = create_database_engine(postgres_url)
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema_name))
        url = (
            make_url(postgres_url)
            .update_query_dict({"options": f"-csearch_path={schema_name}"})
            .render_as_string(hide_password=False)
        )
    engine = create_database_engine(url)
    try:
        config = Config("alembic.ini")
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine, config, url
    finally:
        engine.dispose()
        if admin_engine is not None:
            with admin_engine.begin() as connection:
                connection.execute(DropSchema(schema_name, cascade=True))
            admin_engine.dispose()


def test_records_survive_application_restart(database: tuple[Engine, Config, str]) -> None:
    _, _, url = database
    settings = Settings(database_url=url)
    patient_id = str(uuid4())

    with TestClient(create_app(settings)) as client:
        assert client.post("/v1/patients", json={"patient_id": patient_id}).status_code == 201

    with TestClient(create_app(settings)) as client:
        response = client.get(f"/v1/patients/{patient_id}")
        assert response.status_code == 200
        assert response.json() == {"patient_id": patient_id}
        assert client.post("/v1/patients", json=response.json()).status_code == 200


def test_vital_analysis_survives_restart_without_duplicate_evidence(
    database: tuple[Engine, Config, str],
) -> None:
    _, _, url = database
    settings = Settings(
        database_url=url, vital_ranges={"heart_rate": {"lower": 10.0, "upper": 20.0}}
    )
    patient_id, encounter_id = str(uuid4()), str(uuid4())
    measurement = {
        "source_id": str(uuid4()),
        "patient_id": patient_id,
        "encounter_id": encounter_id,
        "signal": "heart_rate",
        "value": 25.0,
        "unit": "bpm",
        "observed_at": "2020-01-01T12:00:00Z",
        "quality_status": "valid",
    }
    with TestClient(create_app(settings)) as client:
        assert client.post("/v1/patients", json={"patient_id": patient_id}).status_code == 201
        assert (
            client.post(
                "/v1/encounters",
                json={
                    "encounter_id": encounter_id,
                    "patient_id": patient_id,
                    "started_at": "2020-01-01T00:00:00Z",
                },
            ).status_code
            == 201
        )
        original = client.post("/v1/vital-signs/analyze", json=measurement)
        assert original.status_code == 201

    with TestClient(create_app(settings)) as client:
        replay = client.post("/v1/vital-signs/analyze", json=measurement)
        assert replay.status_code == 200
        assert replay.json() == original.json()
        assert client.get(f"/v1/evidence/{original.json()['event_id']}").json() == original.json()


def test_migrations_match_metadata_and_can_be_reapplied(
    database: tuple[Engine, Config, str],
) -> None:
    engine, config, _ = database
    with engine.begin() as connection:
        assert compare_metadata(MigrationContext.configure(connection), metadata) == []
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}
        command.upgrade(config, "head")
        assert compare_metadata(MigrationContext.configure(connection), metadata) == []


def test_database_rejects_unknown_patient(database: tuple[Engine, Config, str]) -> None:
    engine, _, _ = database
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            insert(encounters).values(encounter_id=uuid4(), patient_id=uuid4(), payload={})
        )


def test_database_rejects_evidence_linked_to_wrong_patient(
    database: tuple[Engine, Config, str],
) -> None:
    engine, _, _ = database
    first, second, encounter_id = uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(patients),
            [{"patient_id": first, "payload": {}}, {"patient_id": second, "payload": {}}],
        )
        connection.execute(
            insert(encounters).values(encounter_id=encounter_id, patient_id=first, payload={})
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            insert(evidence).values(
                event_id=uuid4(), encounter_id=encounter_id, patient_id=second, payload={}
            )
        )


def test_distinct_app_instances_coordinate_concurrent_writes(
    database: tuple[Engine, Config, str],
) -> None:
    _, _, url = database
    settings = Settings(database_url=url)
    payload = {"patient_id": str(uuid4())}

    with TestClient(create_app(settings)) as first, TestClient(create_app(settings)) as second:

        def submit(index: int) -> int:
            client = first if index % 2 == 0 else second
            return client.post("/v1/patients", json=payload).status_code

        with ThreadPoolExecutor(max_workers=4) as executor:
            responses = list(executor.map(submit, range(8)))

    assert responses.count(201) == 1
    assert responses.count(200) == 7


def test_database_failure_does_not_leak_connection_details(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'missing' / 'database.db'}")
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/patients", json={"patient_id": str(uuid4())})

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
