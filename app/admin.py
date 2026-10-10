"""Admin commands.

    python -m app.admin add-pilot <pilot_id> <site> <invite_token>
    python -m app.admin add-viewer <viewer_id> <token> [--export]

Only hashes of tokens are stored."""

import hashlib
import sys

from app.db import get_engine, make_sessionmaker
from app.models import Pilot, Viewer


def add_pilot(pilot_id: str, site: str, token: str) -> None:
    with make_sessionmaker(get_engine())() as s:
        s.merge(Pilot(pilot_id=pilot_id, site=site, invite_token_hash=hashlib.sha256(token.encode()).hexdigest()))
        s.commit()


def add_viewer(viewer_id: str, token: str, can_export: bool = False) -> None:
    with make_sessionmaker(get_engine())() as s:
        s.merge(Viewer(viewer_id=viewer_id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                       can_export=can_export))
        s.commit()


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 4 and args[0] == "add-pilot":
        add_pilot(*args[1:])
        print(f"pilot {args[1]} ready")
    elif len(args) in (3, 4) and args[0] == "add-viewer" and args[3:] in ([], ["--export"]):
        add_viewer(args[1], args[2], args[3:] == ["--export"])
        print(f"viewer {args[1]} ready")
    else:
        sys.exit(__doc__)
