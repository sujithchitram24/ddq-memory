"""Create an organisation and print its API key ONCE (only the hash is stored).

Usage: uv run python -m scripts.create_org "Demo Org"
"""
import sys
import uuid

from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.models import Organization
from app.security import hash_api_key, new_api_key


def create_org(session, name: str) -> tuple[uuid.UUID, str]:
    key = new_api_key()
    org = Organization(id=uuid.uuid4(), name=name, api_key_hash=hash_api_key(key))
    session.add(org)
    session.commit()
    return org.id, key


if __name__ == "__main__":
    factory = make_session_factory(make_engine(get_settings().database_url))
    with factory() as s:
        org_id, key = create_org(s, sys.argv[1] if len(sys.argv) > 1 else "Demo Org")
    print(f"org_id={org_id}")
    print(f"API key (shown once, store it safely): {key}")
