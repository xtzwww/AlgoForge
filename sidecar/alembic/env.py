from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.db import _resolve_sync_url

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Resolve the database URL from application settings so migrations and
# the running app always agree on the target. Previously this came from
# a hardcoded `sqlalchemy.url = sqlite:///./algoforge.db` in alembic.ini,
# which meant `alembic upgrade head` migrated a SQLite file while the
# documented production target was PostgreSQL.
#
# Precedence:
#   1. -x db_url=... passed on the alembic command line
#   2. DATABASE_URL from the environment / .env
#   3. sqlalchemy.url from alembic.ini (typically blank)
_cmdline_url = context.get_x_argument(as_dictionary=True).get("db_url")
_resolved_url = _cmdline_url or _resolve_sync_url(settings.database_url or "")
if _resolved_url:
    config.set_main_option("sqlalchemy.url", _resolved_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
import app.models.mistake_book  # noqa: E402, F401 — register on Base.metadata
import app.models.problem  # noqa: E402, F401 — register on Base.metadata
import app.models.review  # noqa: E402, F401 — register on Base.metadata
import app.models.submission  # noqa: E402, F401 — register on Base.metadata
import app.models.test_case  # noqa: E402, F401 — register on Base.metadata
from app.models.base import Base  # noqa: E402

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.

    By skipping the Engine creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
