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
            "DATABASE_URL", "postgresql+psycopg://ft:ft-local-only@localhost:5432/floor_tycoon"
        )
    )
    # "invite": a shared invite link per site (pilot token in the body).
    # "sso": a company SSO proxy in front of the game; the pilot comes from the body
    # and the proxy must set the configured header. The header's value is never stored.
    access_mode: str = field(default_factory=lambda: _env("ACCESS_MODE", "invite"))
    sso_header: str = field(default_factory=lambda: _env("SSO_HEADER", "X-Auth-User"))
    # Raw events are kept for the pilot plus this many months (spec section 8, to confirm).
    retention_months: int = field(default_factory=lambda: int(_env("RETENTION_MONTHS", "12")))
    # Management read: "token" (named viewers, `python -m app.admin add-viewer`) or "sso"
    # (the SSO proxy's header names the viewer). Who may export is configuration too.
    read_auth: str = field(default_factory=lambda: _env("READ_AUTH", "token"))
    read_exporters: frozenset[str] = field(
        default_factory=lambda: frozenset(v for v in _env("READ_EXPORTERS", "").split(",") if v)
    )
    # Web origins allowed to call the API from a browser: the game's domain (and the
    # dashboard's, if separate), comma-separated, e.g. "https://play.example.kr".
    # Empty means no cross-origin calls are allowed.
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(o.strip().rstrip("/") for o in _env("CORS_ORIGINS", "").split(",") if o.strip())
    )
    # Rate limits (app/ratelimit.py): requests per minute; 0 turns one off.
    rate_limit_ip_per_min: int = field(default_factory=lambda: int(_env("RATE_LIMIT_IP_PER_MIN", "600")))
    rate_limit_pilot_per_min: int = field(default_factory=lambda: int(_env("RATE_LIMIT_PILOT_PER_MIN", "6000")))
    # Keys the IP hash; set the same value on every instance.
    rate_limit_secret: str = field(default_factory=lambda: _env("RATE_LIMIT_SECRET", "change-me"))
    # Behind a load balancer, take the client IP from X-Forwarded-For.
    trust_proxy: bool = field(default_factory=lambda: _env("TRUST_PROXY", "false").lower() == "true")
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
