"""Alembic environment. The app passes its own connection (db.migrate); the CLI falls back to db.get_engine()."""
from __future__ import annotations
from alembic import context
import db

target_metadata = db.Base.metadata
APP_TABLES = set(target_metadata.tables)


def include_object(obj, name, type_, reflected, compare_to):
    # LangGraph's checkpoint tables share the database but are managed by langgraph-checkpoint-postgres.
    if type_ == "table" and reflected and compare_to is None:
        return name in APP_TABLES
    return True


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object,
                      render_as_batch=connection.dialect.name == "sqlite", compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:
    with db.get_engine().begin() as conn:
        _run(conn)
