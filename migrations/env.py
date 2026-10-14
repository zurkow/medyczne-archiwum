import os
from logging.config import fileConfig

from alembic import context
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from medical_archive import models  # noqa: F401
from medical_archive.config import Settings
from medical_archive.db import Base, create_database
from medical_archive.rag.index import VectorExtensionUnavailableError, load_sqlite_vec

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    # A URL set in code wins over the environment, so an environment variable can never
    # redirect an upgrade of a staged restore copy to the live database. alembic.ini keeps
    # sqlalchemy.url empty, so the CLI still uses MEDICAL_ARCHIVE_DATABASE_URL.
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url:
        return configured_url
    environment_url = os.environ.get("MEDICAL_ARCHIVE_DATABASE_URL")
    if environment_url:
        return environment_url
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
            if engine.dialect.name == "sqlite":
                try:
                    load_sqlite_vec(connection)
                except VectorExtensionUnavailableError:
                    current_revision = MigrationContext.configure(connection).get_current_revision()
                    connection.rollback()
                    script = ScriptDirectory.from_config(config)
                    if _revision_chain_contains(script, current_revision, "0004_vector_index"):
                        raise
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


def _revision_chain_contains(
    script: ScriptDirectory,
    revision_id: str | None,
    target_revision: str,
) -> bool:
    pending = [revision_id] if revision_id else []
    visited: set[str] = set()
    while pending:
        revision = pending.pop()
        if revision == target_revision:
            return True
        if revision in visited:
            continue
        visited.add(revision)
        migration = script.get_revision(revision)
        if migration is not None and migration.down_revision is not None:
            parents = migration.down_revision
            pending.extend(parents if isinstance(parents, tuple) else [parents])
    return False


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
