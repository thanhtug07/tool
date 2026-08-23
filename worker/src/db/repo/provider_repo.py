"""Provider repository for managing translation/STT/TTS providers and defaults."""

import sqlite3
from typing import Optional, List
from src.db.models import Provider


def _row_to_provider(row: sqlite3.Row) -> Provider:
    return Provider(
        id=row["id"],
        name=row["name"],
        provider_type=row["provider_type"],
        provider_kind=row["provider_kind"],
        enabled=bool(row["enabled"]),
        base_url=row["base_url"],
        model=row["model"],
        config_json=row["config_json"],
        capabilities_json=row["capabilities_json"],
        last_test_status=row["last_test_status"],
        last_test_at=row["last_test_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class ProviderRepo:

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list(self) -> List[Provider]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, name, provider_type, provider_kind, enabled, base_url, model,
                   config_json, capabilities_json, last_test_status, last_test_at,
                   created_at, updated_at
            FROM providers ORDER BY name
            """
        )
        return [_row_to_provider(row) for row in cursor.fetchall()]

    def get(self, provider_id: str) -> Optional[Provider]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, name, provider_type, provider_kind, enabled, base_url, model,
                   config_json, capabilities_json, last_test_status, last_test_at,
                   created_at, updated_at
            FROM providers WHERE id = ?
            """,
            (provider_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _row_to_provider(row)

    def get_default(self, capability: str) -> Optional[Provider]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT p.id, p.name, p.provider_type, p.provider_kind, p.enabled, p.base_url, p.model,
                   p.config_json, p.capabilities_json, p.last_test_status, p.last_test_at,
                   p.created_at, p.updated_at
            FROM provider_defaults pd
            JOIN providers p ON pd.provider_id = p.id
            WHERE pd.capability = ?
            """,
            (capability,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _row_to_provider(row)

    def set_default(self, capability: str, provider_id: str) -> bool:
        cursor = self.conn.execute(
            """
            INSERT INTO provider_defaults (capability, provider_id) VALUES (?, ?)
            ON CONFLICT(capability) DO UPDATE SET provider_id = excluded.provider_id
            """,
            (capability, provider_id),
        )
        return cursor.rowcount > 0

    def upsert(self, provider: Provider) -> None:
        self.conn.execute(
            """
            INSERT INTO providers (
                id, name, provider_type, provider_kind, enabled, base_url, model,
                config_json, capabilities_json, last_test_status, last_test_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                provider_type = excluded.provider_type,
                provider_kind = excluded.provider_kind,
                enabled = excluded.enabled,
                base_url = excluded.base_url,
                model = excluded.model,
                config_json = excluded.config_json,
                capabilities_json = excluded.capabilities_json,
                last_test_status = excluded.last_test_status,
                last_test_at = excluded.last_test_at,
                updated_at = excluded.updated_at
            """,
            (
                provider.id,
                provider.name,
                provider.provider_type,
                provider.provider_kind,
                1 if provider.enabled else 0,
                provider.base_url,
                provider.model,
                provider.config_json,
                provider.capabilities_json,
                provider.last_test_status,
                provider.last_test_at,
                provider.created_at,
                provider.updated_at,
            ),
        )

    def delete(self, provider_id: str) -> bool:
        if provider_id == "free":
            raise ValueError("Built-in 'free' provider cannot be deleted")
        cursor = self.conn.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
        return cursor.rowcount > 0
