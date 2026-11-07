from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from clinical_monitor.core.config import Settings
from clinical_monitor.infrastructure.database.schema import metadata

config = context.config


def run_migrations() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    url = Settings().database_url.get_secret_value()
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
