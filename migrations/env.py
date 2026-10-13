import os
from logging.config import fileConfig

from alembic import context

from medical_archive import models  # noqa: F401
from medical_archive.config import Settings
from medical_archive.db import Base, create_database

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    environment_url = os.environ.get("MEDICAL_ARCHIVE_DATABASE_URL")
    configured_url = config.get_main_option("sqlalchemy.url")
    if environment_url:
        return environment_url
    if configured_url:
        return configured_url
    settings = Settings(testing=True)
    if settings.database_url is None:
        raise ValueError("database_url must be configured.")
    return settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_database(get_database_url())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
