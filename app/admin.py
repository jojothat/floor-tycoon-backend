"""Admin commands. `python -m app.admin add-pilot <pilot_id> <site> <invite_token>`

Only the hash of the invite token is stored."""

import hashlib
import sys

from app.db import get_engine, make_sessionmaker
from app.models import Pilot


def add_pilot(pilot_id: str, site: str, token: str) -> None:
    with make_sessionmaker(get_engine())() as s:
        s.merge(Pilot(pilot_id=pilot_id, site=site, invite_token_hash=hashlib.sha256(token.encode()).hexdigest()))
        s.commit()


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "add-pilot":
        sys.exit(__doc__)
    add_pilot(*sys.argv[2:])
    print(f"pilot {sys.argv[2]} ready")
