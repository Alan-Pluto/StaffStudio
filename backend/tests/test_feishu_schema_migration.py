from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, text

from app.db import database


def _create_legacy_channel_schema(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE channel_bindings (
                    id VARCHAR PRIMARY KEY,
                    tenant_id VARCHAR,
                    agent_id VARCHAR,
                    channel VARCHAR,
                    status VARCHAR,
                    credentials_enc VARCHAR,
                    config_json JSON,
                    external_account_key VARCHAR,
                    identity_scope_key VARCHAR,
                    config_revision INTEGER NOT NULL DEFAULT 0,
                    connected BOOLEAN,
                    created_by_user_id VARCHAR,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE channel_inbound_events (
                    id VARCHAR PRIMARY KEY,
                    tenant_id VARCHAR,
                    binding_id VARCHAR,
                    channel VARCHAR,
                    event_id VARCHAR,
                    payload_json JSON,
                    status VARCHAR,
                    processor_run_id VARCHAR,
                    error VARCHAR,
                    processed_at DATETIME,
                    created_at DATETIME,
                    updated_at DATETIME,
                    UNIQUE (binding_id, event_id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE channel_deliveries (
                    id VARCHAR PRIMARY KEY,
                    tenant_id VARCHAR,
                    binding_id VARCHAR,
                    session_id VARCHAR,
                    message_id VARCHAR,
                    target_json JSON,
                    kind VARCHAR,
                    text VARCHAR,
                    status VARCHAR,
                    attempts INTEGER,
                    next_attempt_at DATETIME,
                    last_error VARCHAR,
                    idempotency_key VARCHAR UNIQUE,
                    delivered_at DATETIME,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO channel_bindings "
                "(id, tenant_id, agent_id, channel, status, config_json) VALUES "
                "('chan_wecom', 'tenant_a', 'agent_a', 'wecom', 'active', '{}')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO channel_inbound_events "
                "(id, tenant_id, binding_id, channel, event_id, payload_json, status) "
                "VALUES ('evt_pk', 'tenant_a', 'chan_wecom', 'wecom', 'remote_evt', "
                "'{\"body\":{\"text\":\"kept\"}}', 'done')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO channel_deliveries "
                "(id, tenant_id, binding_id, session_id, target_json, kind, text, status, "
                "attempts, idempotency_key) VALUES "
                "('delivery_pk', 'tenant_a', 'chan_wecom', 'session_a', '{}', 'reply', "
                "'kept', 'delivered', 1, 'idem_kept')"
            )
        )


def _use_database(monkeypatch, engine, db_path) -> None:
    monkeypatch.setattr(database, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", engine)


def test_feishu_schema_migration_preserves_legacy_channel_data_and_is_idempotent(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "legacy-feishu-schema.db"
    engine = create_engine(f"sqlite:///{db_path}")
    _create_legacy_channel_schema(engine)
    _use_database(monkeypatch, engine, db_path)

    database._migrate_sqlite_skill_schema()
    database._migrate_sqlite_skill_schema()

    with engine.begin() as conn:
        binding_columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(channel_bindings)"))
        }
        inbound_columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(channel_inbound_events)"))
        }
        delivery_columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(channel_deliveries)"))
        }
        assert "provider_tenant_key" in binding_columns
        assert {"config_revision", "target_json", "reaction_id"} <= inbound_columns
        assert "first_attempt_at" in delivery_columns
        target_info = next(
            row
            for row in conn.execute(text("PRAGMA table_info(channel_inbound_events)"))
            if row[1] == "target_json"
        )
        assert target_info[3] == 1
        index_names = {
            row[1]
            for row in conn.execute(text("PRAGMA index_list(channel_inbound_events)"))
        }
        assert "ix_channel_inbound_events_binding_status_created" in index_names
        inbound = conn.execute(
            text(
                "SELECT payload_json, config_revision, target_json "
                "FROM channel_inbound_events WHERE id='evt_pk'"
            )
        ).one()
        assert inbound == ('{"body":{"text":"kept"}}', 0, "{}")
        assert conn.execute(
            text("SELECT text FROM channel_deliveries WHERE id='delivery_pk'")
        ).scalar_one() == "kept"
        assert conn.execute(
            text(
                "SELECT COUNT(*) FROM app_data_migrations "
                "WHERE id=:id"
            ),
            {"id": database._FEISHU_CHANNEL_SCHEMA_MIGRATION_ID},
        ).scalar_one() == 1


def test_feishu_schema_migration_rolls_back_all_columns_on_failure(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "failed-feishu-schema.db"
    engine = create_engine(f"sqlite:///{db_path}")
    _create_legacy_channel_schema(engine)
    _use_database(monkeypatch, engine, db_path)

    def fail_before_delivery_column(_conn, _cursor, statement, *_args):
        if "ADD COLUMN first_attempt_at" in statement:
            raise RuntimeError("injected migration failure")

    event.listen(engine, "before_cursor_execute", fail_before_delivery_column)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        database._migrate_sqlite_skill_schema()
    event.remove(engine, "before_cursor_execute", fail_before_delivery_column)

    with engine.begin() as conn:
        binding_columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(channel_bindings)"))
        }
        inbound_columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(channel_inbound_events)"))
        }
        delivery_columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(channel_deliveries)"))
        }
        assert "provider_tenant_key" not in binding_columns
        assert "config_revision" not in inbound_columns
        assert "target_json" not in inbound_columns
        assert "reaction_id" not in inbound_columns
        assert "first_attempt_at" not in delivery_columns
        assert conn.execute(
            text("SELECT COUNT(*) FROM channel_inbound_events")
        ).scalar_one() == 1
        marker_table = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='app_data_migrations'"
            )
        ).first()
        assert marker_table is None

    database._migrate_sqlite_skill_schema()


def test_feishu_schema_migration_repairs_incomplete_schema_with_marker(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "partial-feishu-schema.db"
    engine = create_engine(f"sqlite:///{db_path}")
    _create_legacy_channel_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE app_data_migrations ("
                "id VARCHAR PRIMARY KEY, applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text("INSERT INTO app_data_migrations (id) VALUES (:id)"),
            {"id": database._FEISHU_CHANNEL_SCHEMA_MIGRATION_ID},
        )
        conn.execute(
            text("ALTER TABLE channel_bindings ADD COLUMN provider_tenant_key VARCHAR")
        )
    _use_database(monkeypatch, engine, db_path)

    database._migrate_sqlite_skill_schema()

    with engine.begin() as conn:
        inbound_columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(channel_inbound_events)"))
        }
        delivery_columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(channel_deliveries)"))
        }
        assert {"config_revision", "target_json", "reaction_id"} <= inbound_columns
        assert "first_attempt_at" in delivery_columns
        assert conn.execute(
            text("SELECT COUNT(*) FROM app_data_migrations WHERE id=:id"),
            {"id": database._FEISHU_CHANNEL_SCHEMA_MIGRATION_ID},
        ).scalar_one() == 1
