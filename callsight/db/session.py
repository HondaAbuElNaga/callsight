from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def make_session_factory(database_url: str) -> sessionmaker:
    # hide_parameters: DB errors must never quote values (transcript text, phone numbers) into logs/tracebacks
    engine = create_engine(database_url, pool_pre_ping=True, hide_parameters=True)
    return sessionmaker(engine, expire_on_commit=False)
