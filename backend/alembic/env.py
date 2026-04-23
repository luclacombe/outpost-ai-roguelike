"""Alembic async env.

Reads settings.DATABASE_URL at startup so migrations run against the same
Postgres the app is pointed at. target_metadata is wired to app.models.Base
so --autogenerate sees the ORM.

`include_object` filters keep autogenerate from fighting with Supabase CLI
migrations: anything outside the `public` schema and any table not declared
in our ORM is treated as not-ours, so no spurious DROPs land in new
revisions.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config import settings

config = context.config
# Escape '%' so configparser's ConfigParser (RawConfigParser variant used by
# Alembic) doesn't treat URL-encoded characters (e.g. '%24' in a Supabase
# DB password) as interpolation tokens.
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.models import Base  # noqa: E402  — must follow settings wiring above

target_metadata = Base.metadata


def _include_object(object_, name, type_, reflected, compare_to):
    """Only autogenerate diffs for tables/constraints we declare.

    Supabase's own schema (auth, storage, realtime, etc.) and anything the
    Supabase CLI migrations add inside `public` must remain invisible to
    Alembic's diff, or autogenerate will emit destructive DROPs.
    """
    if type_ == "table":
        schema = getattr(object_, "schema", None)
        if schema and schema != "public":
            return False
        if reflected and name not in target_metadata.tables:
            return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
