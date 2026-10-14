from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from medical_archive.config import Settings


class Base(DeclarativeBase):
    pass


def create_database(url: str) -> Engine:
    is_sqlite = make_url(url).get_backend_name() == "sqlite"
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _record) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


@lru_cache(maxsize=1)
def _database_engine() -> Engine:
    settings = Settings()
    if settings.database_url is None:
        raise ValueError("database_url must be configured.")
    return create_database(settings.database_url)


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(
        bind=_database_engine(),
        autoflush=False,
        expire_on_commit=False,
    )


def reset_database_engine() -> None:
    if _database_engine.cache_info().currsize:
        _database_engine().dispose()
    _session_factory.cache_clear()
    _database_engine.cache_clear()


def get_session() -> Iterator[Session]:
    with _session_factory()() as session:
        yield session
