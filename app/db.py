from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base

# Raw events are append-only. Only the retention job may delete them, and it must
# set `app.retention = 'on'` in its transaction first. Updates are never allowed.
APPEND_ONLY_SQL = """
CREATE OR REPLACE FUNCTION events_append_only() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' AND current_setting('app.retention', true) = 'on' THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'events are append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_append_only ON events;
CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON events
  FOR EACH ROW EXECUTE FUNCTION events_append_only();
"""


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(APPEND_ONLY_SQL))


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


if __name__ == "__main__":
    init_db(get_engine())
    print("tables created")
