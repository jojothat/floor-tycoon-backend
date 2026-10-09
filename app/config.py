"""Settings. Everything the spec leaves open (section 10) is configuration here,
not code: the database (so any Korean-region provider), the access method and
the retention period."""

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    # Postgres in a Korean region. Local runs and tests use Postgres via Docker.
    database_url: str = field(
        default_factory=lambda: _env(
            "DATABASE_URL", "postgresql+psycopg://ft@localhost:5432/floor_tycoon"
        )
    )
    # "invite": a shared invite link per site (pilot token in the body).
    # "sso": a company SSO proxy in front of the game; the pilot comes from the body
    # and the proxy must set the configured header. The header's value is never stored.
    access_mode: str = field(default_factory=lambda: _env("ACCESS_MODE", "invite"))
    sso_header: str = field(default_factory=lambda: _env("SSO_HEADER", "X-Auth-User"))
    # Raw events are kept for the pilot plus this many months (spec section 8, to confirm).
    retention_months: int = field(default_factory=lambda: int(_env("RETENTION_MONTHS", "12")))
    write_key_ttl_minutes: int = field(
        default_factory=lambda: int(_env("WRITE_KEY_TTL_MINUTES", "120"))
    )
    max_batch_bytes: int = field(default_factory=lambda: int(_env("MAX_BATCH_BYTES", "65536")))
    min_group_size: int = field(default_factory=lambda: int(_env("MIN_GROUP_SIZE", "10")))
    scenarios_path: Path = field(
        default_factory=lambda: Path(_env("SCENARIOS_PATH", str(ROOT / "sample" / "scenarios.json")))
    )
    # The id of "The team grows to 14", always dealt in z7. Optional: if unset it is
    # found in the scenario library (see app/deal.py).
    deal_z7_scenario_id: int | None = field(
        default_factory=lambda: int(v) if (v := os.environ.get("DEAL_Z7_SCENARIO_ID")) else None
    )


def get_settings() -> Settings:
    return Settings()
