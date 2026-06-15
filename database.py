import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


JsonDict = dict[str, Any]

SCHEMA_VERSION = 1
_DB_PATH: Path | None = None
_DB_LOCK = threading.RLock()


def configure_database(db_path: Path) -> None:
    global _DB_PATH
    _DB_PATH = db_path
    initialize_schema()


def db_path() -> Path:
    if _DB_PATH is None:
        raise RuntimeError("Database has not been configured.")
    return _DB_PATH


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(db_path(), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize_schema() -> None:
    with _DB_LOCK, connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bosses (
                boss_id TEXT PRIMARY KEY,
                legacy_id TEXT,
                name TEXT NOT NULL,
                server TEXT NOT NULL DEFAULT 'Austeja',
                channel TEXT NOT NULL DEFAULT '1',
                element TEXT NOT NULL DEFAULT 'ไม่ระบุ',
                race TEXT NOT NULL DEFAULT 'ไม่ระบุ',
                cooldown_minutes INTEGER NOT NULL DEFAULT 0,
                next_spawn TEXT,
                alert_sent INTEGER NOT NULL DEFAULT 0,
                alert_message_id INTEGER,
                extra_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS boss_groups (
                user_id TEXT NOT NULL,
                boss_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (user_id, boss_id)
            );

            CREATE TABLE IF NOT EXISTS message_delete_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                delete_at TEXT NOT NULL,
                extra_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )


def read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def migrate_from_json(
    boss_config_path: Path,
    boss_groups_path: Path,
    message_delete_queue_path: Path,
) -> None:
    with _DB_LOCK, connect() as connection:
        migrated = connection.execute(
            "SELECT value FROM metadata WHERE key = 'json_migrated'"
        ).fetchone()
        if migrated and migrated["value"] == "1":
            return

        boss_count = connection.execute("SELECT COUNT(*) FROM bosses").fetchone()[0]
        group_count = connection.execute("SELECT COUNT(*) FROM boss_groups").fetchone()[0]
        queue_count = connection.execute("SELECT COUNT(*) FROM message_delete_queue").fetchone()[0]

        if boss_count == 0:
            boss_data = read_json_file(boss_config_path, {})
            if isinstance(boss_data, dict):
                save_boss_data(boss_data, connection=connection)

        if group_count == 0:
            groups = read_json_file(boss_groups_path, {})
            if isinstance(groups, dict):
                save_boss_groups(groups, connection=connection)

        if queue_count == 0:
            queue = read_json_file(message_delete_queue_path, [])
            if isinstance(queue, list):
                save_message_delete_queue(queue, connection=connection)

        connection.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('json_migrated', '1')"
        )


def boss_from_row(row: sqlite3.Row) -> JsonDict:
    try:
        extra = json.loads(row["extra_json"] or "{}")
    except json.JSONDecodeError:
        extra = {}

    boss = dict(extra) if isinstance(extra, dict) else {}
    boss.update(
        {
            "name": row["name"],
            "element": row["element"],
            "race": row["race"],
            "cooldown_minutes": int(row["cooldown_minutes"]),
            "next_spawn": row["next_spawn"],
            "alert_sent": bool(row["alert_sent"]),
            "channel": row["channel"],
            "server": row["server"],
        }
    )

    if row["legacy_id"]:
        boss["legacy_id"] = row["legacy_id"]
    if row["alert_message_id"] is not None:
        boss["alert_message_id"] = int(row["alert_message_id"])

    return boss


def load_boss_data() -> dict[str, JsonDict]:
    with _DB_LOCK, connect() as connection:
        rows = connection.execute("SELECT * FROM bosses ORDER BY rowid").fetchall()
    return {str(row["boss_id"]): boss_from_row(row) for row in rows}


def save_boss_data(data: dict[str, JsonDict], connection: sqlite3.Connection | None = None) -> None:
    def write(target: sqlite3.Connection) -> None:
        target.execute("DELETE FROM bosses")
        for boss_id, boss in data.items():
            known_keys = {
                "name",
                "legacy_id",
                "server",
                "channel",
                "element",
                "race",
                "cooldown_minutes",
                "next_spawn",
                "alert_sent",
                "alert_message_id",
            }
            extra = {key: value for key, value in boss.items() if key not in known_keys}
            target.execute(
                """
                INSERT INTO bosses (
                    boss_id, legacy_id, name, server, channel, element, race,
                    cooldown_minutes, next_spawn, alert_sent, alert_message_id, extra_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(boss_id),
                    optional_text(boss.get("legacy_id")),
                    required_text(boss.get("name"), str(boss_id)),
                    required_text(boss.get("server"), "Austeja"),
                    required_text(boss.get("channel"), "1"),
                    required_text(boss.get("element"), "ไม่ระบุ"),
                    required_text(boss.get("race"), "ไม่ระบุ"),
                    int_or_default(boss.get("cooldown_minutes"), 0),
                    optional_text(boss.get("next_spawn")),
                    1 if boss.get("alert_sent") else 0,
                    optional_int(boss.get("alert_message_id")),
                    json.dumps(extra, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    if connection is not None:
        write(connection)
        return

    with _DB_LOCK, connect() as target:
        write(target)


def save_boss_record(boss_id: str, boss: JsonDict) -> None:
    known_keys = {
        "name",
        "legacy_id",
        "server",
        "channel",
        "element",
        "race",
        "cooldown_minutes",
        "next_spawn",
        "alert_sent",
        "alert_message_id",
    }
    extra = {key: value for key, value in boss.items() if key not in known_keys}
    with _DB_LOCK, connect() as connection:
        connection.execute(
            """
            INSERT INTO bosses (
                boss_id, legacy_id, name, server, channel, element, race,
                cooldown_minutes, next_spawn, alert_sent, alert_message_id, extra_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(boss_id) DO UPDATE SET
                legacy_id = excluded.legacy_id,
                name = excluded.name,
                server = excluded.server,
                channel = excluded.channel,
                element = excluded.element,
                race = excluded.race,
                cooldown_minutes = excluded.cooldown_minutes,
                next_spawn = excluded.next_spawn,
                alert_sent = excluded.alert_sent,
                alert_message_id = excluded.alert_message_id,
                extra_json = excluded.extra_json
            """,
            (
                str(boss_id),
                optional_text(boss.get("legacy_id")),
                required_text(boss.get("name"), str(boss_id)),
                required_text(boss.get("server"), "Austeja"),
                required_text(boss.get("channel"), "1"),
                required_text(boss.get("element"), "ไม่ระบุ"),
                required_text(boss.get("race"), "ไม่ระบุ"),
                int_or_default(boss.get("cooldown_minutes"), 0),
                optional_text(boss.get("next_spawn")),
                1 if boss.get("alert_sent") else 0,
                optional_int(boss.get("alert_message_id")),
                json.dumps(extra, ensure_ascii=False, separators=(",", ":")),
            ),
        )


def delete_boss_record(boss_id: str) -> None:
    with _DB_LOCK, connect() as connection:
        connection.execute("DELETE FROM bosses WHERE boss_id = ?", (str(boss_id),))


def claim_boss_spawn_alert(boss_id: str, next_spawn: str) -> bool:
    with _DB_LOCK, connect() as connection:
        cursor = connection.execute(
            """
            UPDATE bosses
            SET alert_sent = 1, alert_message_id = NULL
            WHERE boss_id = ?
              AND next_spawn = ?
              AND alert_sent = 0
              AND cooldown_minutes != 0
            """,
            (boss_id, next_spawn),
        )
        return cursor.rowcount == 1


def complete_boss_spawn_alert(boss_id: str, next_spawn: str, alert_message_id: int) -> None:
    with _DB_LOCK, connect() as connection:
        connection.execute(
            """
            UPDATE bosses
            SET alert_sent = 1, alert_message_id = ?
            WHERE boss_id = ?
              AND next_spawn = ?
              AND alert_sent = 1
            """,
            (int(alert_message_id), boss_id, next_spawn),
        )


def rollback_boss_spawn_alert_claim(boss_id: str, next_spawn: str) -> None:
    with _DB_LOCK, connect() as connection:
        connection.execute(
            """
            UPDATE bosses
            SET alert_sent = 0, alert_message_id = NULL
            WHERE boss_id = ?
              AND next_spawn = ?
              AND alert_sent = 1
              AND alert_message_id IS NULL
            """,
            (boss_id, next_spawn),
        )


def load_boss_groups() -> dict[str, list[str]]:
    with _DB_LOCK, connect() as connection:
        rows = connection.execute(
            "SELECT user_id, boss_id FROM boss_groups ORDER BY user_id, position"
        ).fetchall()

    groups: dict[str, list[str]] = {}
    for row in rows:
        groups.setdefault(str(row["user_id"]), []).append(str(row["boss_id"]))
    return groups


def save_boss_groups(groups: dict[str, list[str]], connection: sqlite3.Connection | None = None) -> None:
    def write(target: sqlite3.Connection) -> None:
        target.execute("DELETE FROM boss_groups")
        for user_id, boss_ids in groups.items():
            if not isinstance(boss_ids, list):
                continue
            for position, boss_id in enumerate(boss_ids):
                target.execute(
                    "INSERT INTO boss_groups (user_id, boss_id, position) VALUES (?, ?, ?)",
                    (str(user_id), str(boss_id), position),
                )

    if connection is not None:
        write(connection)
        return

    with _DB_LOCK, connect() as target:
        write(target)


def clear_boss_group(user_id: int | str) -> int:
    with _DB_LOCK, connect() as connection:
        cursor = connection.execute(
            "DELETE FROM boss_groups WHERE user_id = ?",
            (str(user_id),),
        )
        return cursor.rowcount


def load_message_delete_queue() -> list[JsonDict]:
    with _DB_LOCK, connect() as connection:
        rows = connection.execute(
            "SELECT * FROM message_delete_queue ORDER BY id"
        ).fetchall()

    queue: list[JsonDict] = []
    for row in rows:
        try:
            extra = json.loads(row["extra_json"] or "{}")
        except json.JSONDecodeError:
            extra = {}

        item = dict(extra) if isinstance(extra, dict) else {}
        item.update(
            {
                "message_id": int(row["message_id"]),
                "channel_id": int(row["channel_id"]),
                "delete_at": row["delete_at"],
            }
        )
        queue.append(item)
    return queue


def save_message_delete_queue(queue: list[JsonDict], connection: sqlite3.Connection | None = None) -> None:
    def write(target: sqlite3.Connection) -> None:
        target.execute("DELETE FROM message_delete_queue")
        for item in queue:
            if not isinstance(item, dict):
                continue
            try:
                message_id = int(item["message_id"])
                channel_id = int(item["channel_id"])
                delete_at = str(item["delete_at"])
            except (KeyError, TypeError, ValueError):
                continue

            extra = {
                key: value
                for key, value in item.items()
                if key not in {"message_id", "channel_id", "delete_at"}
            }
            target.execute(
                """
                INSERT INTO message_delete_queue (message_id, channel_id, delete_at, extra_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    message_id,
                    channel_id,
                    delete_at,
                    json.dumps(extra, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    if connection is not None:
        write(connection)
        return

    with _DB_LOCK, connect() as target:
        write(target)


def required_text(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
