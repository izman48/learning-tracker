"""Alembic environment. The database URL comes from the Config built by
learning_tracker.db.migrate.alembic_config; nothing here reads the environment."""

from alembic import context
from sqlalchemy import create_engine

from learning_tracker.db.models import Base

url = context.config.get_main_option("sqlalchemy.url")
if not url:
    raise RuntimeError("no database URL: build the Alembic config with alembic_config(url)")

engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
