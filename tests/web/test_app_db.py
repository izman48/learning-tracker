"""The app opens its database engine at startup and closes it at shutdown."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text

from learning_tracker.web.app import create_app
from learning_tracker.web.config import WebConfig


def test_startup_provides_a_working_session_factory(migrated_database_url: str) -> None:
    config = WebConfig(
        database_url=migrated_database_url,
        session_key=b"k" * 32,
        session_lifetime=timedelta(days=1),
        public_origin="https://reads.example.com",
        ip_key=b"i" * 32,
        invite_key=b"v" * 32,
        reset_key=b"r" * 32,
    )
    app = create_app(config)

    async def one() -> int:
        async with app.state.session_factory() as session:
            return int((await session.execute(text("select 1"))).scalar_one())

    with TestClient(app) as client:
        assert client.portal is not None  # set while the lifespan runs
        assert client.portal.call(one) == 1
