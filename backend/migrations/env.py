import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_settings
from app.db import CONNECT_ARGS
from app.schema import metadata

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)
log = logging.getLogger("alembic.env")

target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Tests pass their own connection; everything else connects with DATABASE_URL.
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    log.info("Connecting to the database")
    engine = create_engine(
        get_settings().sqlalchemy_url, poolclass=pool.NullPool, connect_args=CONNECT_ARGS
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
