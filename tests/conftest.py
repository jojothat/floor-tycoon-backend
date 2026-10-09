import hashlib
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://ft@localhost:5432/floor_tycoon_test"),
)
os.environ.setdefault("SCENARIOS_PATH", str(ROOT / "sample" / "scenarios.json"))
# Off for the suite (one test client makes hundreds of calls); tests/test_ratelimit.py turns them on.
os.environ.setdefault("RATE_LIMIT_IP_PER_MIN", "0")
os.environ.setdefault("RATE_LIMIT_PILOT_PER_MIN", "0")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_engine, init_db, make_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Pilot, Viewer  # noqa: E402

PILOT_TOKEN = "test-invite-token"
TOKEN = "viewer-token"
EXPORT_TOKEN = "exporter-token"


@pytest.fixture(scope="session")
def engine():
    eng = get_engine(get_settings().database_url)
    Base.metadata.drop_all(eng)
    init_db(eng)
    return eng


@pytest.fixture(autouse=True)
def clean(engine):
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as c:
        c.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with make_sessionmaker(engine)() as s:
        s.add(
            Pilot(
                pilot_id="pilot-1",
                site="site-a",
                invite_token_hash=hashlib.sha256(PILOT_TOKEN.encode()).hexdigest(),
            )
        )
        s.commit()
    yield


@pytest.fixture
def db(engine):
    with make_sessionmaker(engine)() as s:
        yield s


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def new_session(client):
    def make(role="CE", **extra):
        body = {
            "pilot_token": PILOT_TOKEN,
            "anon_player_id": "anon-player-0001",
            "role_type": role,
            "game_version": "test",
            "flow": "quick",
            "language": "ko",
            "device_class": "phone",
            "viewport_height": 390,
            **extra,
        }
        r = client.post("/v1/sessions", json=body)
        assert r.status_code == 201, r.text
        out = r.json()
        out["headers"] = {"Authorization": f"Bearer {out['write_key']}"}
        return out

    return make


def load_json(path: Path):
    return json.loads(path.read_text())


@pytest.fixture
def viewers(db):
    """Two management viewers for the read API; only the analyst may export."""
    db.add(Viewer(viewer_id="manager-1", token_hash=hashlib.sha256(TOKEN.encode()).hexdigest()))
    db.add(Viewer(viewer_id="analyst-1", token_hash=hashlib.sha256(EXPORT_TOKEN.encode()).hexdigest(), can_export=True))
    db.commit()
