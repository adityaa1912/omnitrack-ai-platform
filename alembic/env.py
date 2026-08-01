"""Alembic migration environment for OmniTrack.

The database URL is resolved from the application settings: PostgreSQL when
``OMNITRACK_POSTGRES_URL`` is set, otherwise the SQLite file (``OMNITRACK_DB_PATH``
or the default ``inference_data.db``). ``target_metadata`` is the application's
models' metadata, so ``--autogenerate`` diffs against ``backend/models.py``.

Importing ``backend.models`` does not construct the service or create tables
(see ``backend/__init__.py``), so Alembic owns the schema without fighting the
app's dev-convenience ``create_all``.
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the project root importable regardless of where alembic is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve the database URL. Prefer the app's settings (which honour
# OMNITRACK_POSTGRES_URL); fall back to OMNITRACK_DB_PATH / default SQLite so
# migrations can run without the full settings environment.
def _resolve_url() -> str:
    try:
        from backend.settings import get_settings

        return get_settings().database_url
    except Exception:  # noqa: BLE001 - settings may be unavailable in bare envs
        db_path = os.environ.get("OMNITRACK_DB_PATH", "inference_data.db")
        return f"sqlite:///{db_path}"


config.set_main_option("sqlalchemy.url", _resolve_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL for the migrations without a live DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite: safe ALTER via batch operations
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
