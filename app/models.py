"""The data model in spec section 4.

Every play is one session; everything else hangs off it. Raw `events` are stored
as sent and never edited. `moments`, `edits`, `additions`, `add_declines` and
`floor` are derived and can be rebuilt from the events at any time.

No identity is stored anywhere: no names, emails, employee numbers or exact
locations, and never the character look.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLE_TYPES = ("CE", "PSE", "IE", "Sales", "Support", "Lead", "none")
FLOWS = ("quick", "classic")
ZONES = ("z1", "z2", "z3", "z4", "z5", "z6", "z7")


class Base(DeclarativeBase):
    pass


class Pilot(Base):
    __tablename__ = "pilots"

    pilot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    site: Mapped[str] = mapped_column(String(64))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    deal_version: Mapped[str] = mapped_column(String(32), default="default-v1")
    invite_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)


class GameSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(f"role_type IN {ROLE_TYPES}", name="sessions_role_type"),
        CheckConstraint(f"flow IN {FLOWS}", name="sessions_flow"),
    )

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    anon_player_id: Mapped[str] = mapped_column(String(64), index=True)
    pilot_id: Mapped[str] = mapped_column(ForeignKey("pilots.pilot_id"), index=True)
    role_type: Mapped[str] = mapped_column(String(16), default="none")
    device_class: Mapped[str | None] = mapped_column(String(16))
    viewport_height_bucket: Mapped[str | None] = mapped_column(String(16))
    language: Mapped[str | None] = mapped_column(String(16))
    game_version: Mapped[str | None] = mapped_column(String(32))
    flow: Mapped[str] = mapped_column(String(8), default="quick")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    write_key_hash: Mapped[str] = mapped_column(String(64))
    write_key_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Set by the retention job when this play's raw events were deleted. Derived tables and
    # the quality score are kept, and are no longer rebuilt.
    events_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Deal(Base):
    __tablename__ = "deals"

    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id"), primary_key=True)
    # Scenario ids in play order, and the zone of each, index for index.
    scenario_ids: Mapped[list[int]] = mapped_column(JSONB)
    zones: Mapped[list[str]] = mapped_column(JSONB)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="events_session_seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    ev: Mapped[str] = mapped_column(String(32))
    m: Mapped[str | None] = mapped_column(String(4))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    t: Mapped[int] = mapped_column(BigInteger)  # client ms since session start
    server_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    unknown_ev: Mapped[bool] = mapped_column(Boolean, default=False)
    # Free text (classic skip "What would work instead?"). Never shown without review.
    has_text: Mapped[bool] = mapped_column(Boolean, default=False)


class Moment(Base):
    __tablename__ = "moments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id"), index=True)
    scenario_id: Mapped[int | None] = mapped_column(Integer)
    zone: Mapped[str] = mapped_column(String(4))
    play_order: Mapped[int] = mapped_column(Integer)
    previewed: Mapped[list[str]] = mapped_column(JSONB, default=list)
    first_preview: Mapped[str | None] = mapped_column(String(2))
    # a-d, "skip", or NULL if the player left before deciding.
    chosen: Mapped[str | None] = mapped_column(String(4))
    time_to_decide_ms: Mapped[int | None] = mapped_column(Integer)
    rethinks: Mapped[int] = mapped_column(Integer, default=0)


class Edit(Base):
    __tablename__ = "edits"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id"), index=True)
    zone: Mapped[str] = mapped_column(String(4))
    from_option: Mapped[str] = mapped_column(String(2))
    to_option: Mapped[str] = mapped_column(String(2))  # "0" = changed to a skip


class Addition(Base):
    __tablename__ = "additions"
    __table_args__ = (
        CheckConstraint(
            "how_placed IN ('gap', 'end', 'desks_smaller', 'desks_share', 'swap')",
            name="additions_how_placed",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id"), index=True)
    item_id: Mapped[str] = mapped_column(String(64))
    how_placed: Mapped[str] = mapped_column(String(16))
    zone: Mapped[str | None] = mapped_column(String(4))
    swapped_out_option: Mapped[str | None] = mapped_column(String(2))
    shuffles: Mapped[int] = mapped_column(Integer, default=0)
    removed: Mapped[bool] = mapped_column(Boolean, default=False)


class AddDecline(Base):
    __tablename__ = "add_declines"
    __table_args__ = (CheckConstraint("where_ IN ('end', 'gap')", name="add_declines_where"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id"), index=True)
    where_: Mapped[str] = mapped_column("where_", String(4))
    zone: Mapped[str | None] = mapped_column(String(4))


class Floor(Base):
    __tablename__ = "floor"

    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id"), primary_key=True)
    final_picks: Mapped[dict] = mapped_column(JSONB, default=dict)  # zone -> a-d or "0"
    skips: Mapped[list[str]] = mapped_column(JSONB, default=list)  # zones left as they were
    addition: Mapped[dict | None] = mapped_column(JSONB)
    palette_id: Mapped[str | None] = mapped_column(String(32))
    # Filled by POST /floor (build step 2): use %, happiness %, desk use %, peak, busy % per place,
    # seats, fab trips.
    sim: Mapped[dict | None] = mapped_column(JSONB)


class WallPost(Base):
    __tablename__ = "wall_posts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    anon_player_id: Mapped[str] = mapped_column(String(64), unique=True)  # latest only
    pilot_id: Mapped[str] = mapped_column(ForeignKey("pilots.pilot_id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id"))
    deal: Mapped[list[int]] = mapped_column(JSONB)
    picks: Mapped[dict] = mapped_column(JSONB)
    skips: Mapped[list[str]] = mapped_column(JSONB, default=list)
    adds: Mapped[list | None] = mapped_column(JSONB)
    deskfit: Mapped[str | None] = mapped_column(String(16))
    swaps: Mapped[dict | None] = mapped_column(JSONB)
    palette: Mapped[str | None] = mapped_column(String(32))
    use_pct: Mapped[int | None] = mapped_column(Integer)
    happiness_pct: Mapped[int | None] = mapped_column(Integer)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WallView(Base):
    __tablename__ = "wall_views"

    viewer_anon_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    wall_post_id: Mapped[int] = mapped_column(ForeignKey("wall_posts.id"), primary_key=True)
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Quality(Base):
    __tablename__ = "quality"

    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id"), primary_key=True)
    score: Mapped[int] = mapped_column(Integer)
    flags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    weight: Mapped[float] = mapped_column(Float)  # 0, 0.5 or 1 from the score
    # False for unfinished and repeat plays: kept for drop-off and skip analysis only.
    for_preferences: Mapped[bool] = mapped_column(Boolean)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Viewer(Base):
    """A named management viewer (read API). Not a player: players are never named."""

    __tablename__ = "viewers"

    viewer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    can_export: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(Base):
    """Who viewed and who exported (spec section 10)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    viewer_id: Mapped[str] = mapped_column(String(64), index=True)
    endpoint: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    export: Mapped[bool] = mapped_column(Boolean, default=False)
    refused: Mapped[bool] = mapped_column(Boolean, default=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RateCounter(Base):
    """Requests per key per one-minute window (app/ratelimit.py). Never holds an IP address."""

    __tablename__ = "rate_counters"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    window: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
