import json
import logging
import os
import asyncio
import unicodedata
import re
import random
import tempfile
import threading
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

import database

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "boss_config.json"
DATABASE_PATH = BASE_DIR / "bot.db"
MESSAGE_DELETE_QUEUE_PATH = BASE_DIR / "message_delete_queue.json"
BOSS_GROUPS_PATH = BASE_DIR / "boss_groups.json"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
BOSSES_PER_PAGE = 25
STATUS_BOSSES_PER_PAGE = 5
GROUP_BOSSES_PER_PAGE = 25
MAX_GROUP_BOSSES = 5
DEFAULT_BOSS_SERVER = "Austeja"
DEFAULT_BOSS_SERVERS = ("Austeja", "Laima", "Jurate")
LOCAL_TIMEZONE = timezone(timedelta(hours=7))
STATUS_TABLE_LIMIT = 1900
ACTIVE_ALERTS_PER_PAGE = 25
STATUS_TABLE_COLUMNS = [
    ("บอส", 34),
    ("เวลาที่บันทึก", 26),
    ("Server", 12),
    ("ช่องเกิด", 16),
    ("ธาตุ", 12),
    ("เผ่า", 16),
    ("คูลดาวน์", 18),
]

load_dotenv(BASE_DIR / ".env")


def env_int(key: str, default: int = 0) -> int:
    value = os.getenv(key)
    if value is None or not value.strip():
        return default

    try:
        return int(value)
    except ValueError:
        return default


def env_bool(key: str, default: bool = False) -> bool:
    value = os.getenv(key)
    if value is None or not value.strip():
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


def configured_boss_servers() -> tuple[str, ...]:
    servers = list(DEFAULT_BOSS_SERVERS)
    for raw_server in os.getenv("EXTRA_BOSS_SERVERS", "").split(","):
        server = raw_server.strip()
        if server and server.lower() not in {item.lower() for item in servers}:
            servers.append(server)
    return tuple(servers)


TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = env_int("DISCORD_CHANNEL_ID")
SUPPORTED_BOSS_SERVERS = configured_boss_servers()
SERVER_ALERT_CHANNEL_IDS = {
    server: env_int(f"DISCORD_{server.upper()}_CHANNEL_ID", CHANNEL_ID)
    for server in SUPPORTED_BOSS_SERVERS
}
GUILD_ID = env_int("DISCORD_GUILD_ID")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")
ALERT_GRACE_MINUTES = env_int("ALERT_GRACE_MINUTES", 5)
MISSING_COOLDOWN_REMINDER_HOURS = env_int("MISSING_COOLDOWN_REMINDER_HOURS", 12)
ENABLE_MISSING_COOLDOWN_REMINDERS = env_bool("ENABLE_MISSING_COOLDOWN_REMINDERS", False)
UNRECORDED_BOSS_RESET_MINUTES = env_int("UNRECORDED_BOSS_RESET_MINUTES", 45)
ALERT_MESSAGE_DELETE_DELAY_SECONDS = 60
ALERT_MESSAGE_DELETE_NOTICE = "ข้อความนี้กำลังจะถูกลบในอีก 1 นาที"
MISSING_COOLDOWN_ALERTS_PER_PAGE = env_int("MISSING_COOLDOWN_ALERTS_PER_PAGE", 10)
CHANNEL_SUFFIX_PATTERN = re.compile(r"_ch[a-z0-9]+$", re.IGNORECASE)
SERVER_SUFFIX_PATTERN = re.compile(
    r"_(" + "|".join(re.escape(server.lower()) for server in SUPPORTED_BOSS_SERVERS) + r")$",
    re.IGNORECASE,
)
JsonDict = dict[str, Any]
JsonList = list[Any]
BOSS_DATA_LOCK = threading.RLock()
BOSS_GROUPS_LOCK = threading.RLock()
MESSAGE_DELETE_QUEUE_LOCK = threading.RLock()
ENV_FILE_LOCK = threading.RLock()

log_handler = RotatingFileHandler(
    BASE_DIR / "bot.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[log_handler],
)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)
database.configure_database(DATABASE_PATH)
database.migrate_from_json(CONFIG_PATH, BOSS_GROUPS_PATH, MESSAGE_DELETE_QUEUE_PATH)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def discord_time(dt: datetime, style: str = "F") -> str:
    return f"<t:{int(dt.timestamp())}:{style}>"


def write_text_atomic(path: Path, text: str) -> None:
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_name = temp_file.name
            temp_file.write(text)

        os.replace(temp_name, path)
    except OSError:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise


def load_boss_data() -> JsonDict:
    with BOSS_DATA_LOCK:
        return database.load_boss_data()


def save_boss_data(data: JsonDict) -> None:
    with BOSS_DATA_LOCK:
        database.save_boss_data(data)


def save_boss_record(boss_id: str, boss: JsonDict) -> None:
    with BOSS_DATA_LOCK:
        database.save_boss_record(boss_id, boss)


def delete_boss_record(boss_id: str) -> None:
    with BOSS_DATA_LOCK:
        database.delete_boss_record(boss_id)


def load_message_delete_queue() -> list[JsonDict]:
    try:
        with MESSAGE_DELETE_QUEUE_LOCK:
            data = database.load_message_delete_queue()
    except (json.JSONDecodeError, OSError) as error:
        logging.exception("Could not load message delete queue: %s", error)
        return []

    return [item for item in data if isinstance(item, dict)]


def save_message_delete_queue(queue: list[JsonDict]) -> None:
    with MESSAGE_DELETE_QUEUE_LOCK:
        database.save_message_delete_queue(queue)


def load_boss_groups() -> dict[str, list[str]]:
    try:
        with BOSS_GROUPS_LOCK:
            data = database.load_boss_groups()
    except (json.JSONDecodeError, OSError) as error:
        logging.exception("Could not load boss groups: %s", error)
        return {}

    groups: dict[str, list[str]] = {}
    for user_id, boss_ids in data.items():
        if not isinstance(boss_ids, list):
            continue
        groups[str(user_id)] = [str(boss_id) for boss_id in boss_ids[:MAX_GROUP_BOSSES]]
    return groups


def save_boss_groups(groups: dict[str, list[str]]) -> None:
    with BOSS_GROUPS_LOCK:
        database.save_boss_groups(groups)


def save_env_value(key: str, value: str) -> None:
    env_path = BASE_DIR / ".env"
    with ENV_FILE_LOCK:
        lines = []
        found = False

        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8-sig").splitlines()

        for index, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[index] = f"{key}={value}"
                found = True
                break

        if not found:
            lines.append(f"{key}={value}")

        write_text_atomic(env_path, "\n".join(lines) + "\n")


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, TIME_FORMAT)


def format_time(value: datetime) -> str:
    return value.strftime(TIME_FORMAT)


boss_data = load_boss_data()
slash_commands_synced = False
persistent_views_registered = False
processed_alert_message_ids: set[int] = set()
alert_record_locks: dict[int, asyncio.Lock] = {}
boss_spawn_check_lock = asyncio.Lock()


def reload_boss_data() -> None:
    global boss_data
    boss_data = load_boss_data()


def boss_items() -> list[tuple[str, dict]]:
    return list(boss_data.items())


def boss_sort_key(item: tuple[str, dict]) -> tuple[int, str, str, str]:
    boss_id, boss = item
    match = re.search(r"lv(\d+)", boss_id, re.IGNORECASE)
    level = int(match.group(1)) if match else 999999
    return (
        level,
        normalize_boss_server(str(boss.get("server") or DEFAULT_BOSS_SERVER)).lower(),
        boss_id_base(boss_id),
        channel_id_suffix(str(boss.get("channel") or "")),
    )


def sorted_boss_items() -> list[tuple[str, dict]]:
    return sorted(boss_items(), key=boss_sort_key)


def total_pages() -> int:
    return max(1, (len(boss_data) + BOSSES_PER_PAGE - 1) // BOSSES_PER_PAGE)


def page_slice(page: int) -> list[tuple[str, dict]]:
    start = page * BOSSES_PER_PAGE
    return boss_items()[start : start + BOSSES_PER_PAGE]


def normalize_page(page: int) -> int:
    return max(0, min(page, total_pages() - 1))


def boss_items_for_server(server: str | None) -> list[tuple[str, dict]]:
    boss_server = normalize_boss_server(server)
    return [
        (boss_id, boss)
        for boss_id, boss in sorted_boss_items()
        if normalize_boss_server(str(boss.get("server") or DEFAULT_BOSS_SERVER)) == boss_server
    ]


def group_total_pages(server: str | None = None) -> int:
    return max(1, (len(boss_items_for_server(server)) + GROUP_BOSSES_PER_PAGE - 1) // GROUP_BOSSES_PER_PAGE)


def group_page_slice(page: int, server: str | None = None) -> list[tuple[str, dict]]:
    items = boss_items_for_server(server)
    start = page * GROUP_BOSSES_PER_PAGE
    return items[start : start + GROUP_BOSSES_PER_PAGE]


def normalize_group_page(page: int, server: str | None = None) -> int:
    return max(0, min(page, group_total_pages(server) - 1))


def status_total_pages(server: str | None = None) -> int:
    items = boss_items_for_server(server)
    return max(1, (len(items) + STATUS_BOSSES_PER_PAGE - 1) // STATUS_BOSSES_PER_PAGE)


def status_page_slice(page: int, server: str | None = None) -> list[tuple[str, dict]]:
    items = boss_items_for_server(server)
    start = page * STATUS_BOSSES_PER_PAGE
    return items[start : start + STATUS_BOSSES_PER_PAGE]


def normalize_status_page(page: int, server: str | None = None) -> int:
    return max(0, min(page, status_total_pages(server) - 1))


def boss_detail_line(boss: dict) -> str:
    element = boss.get("element") or "ไม่ระบุ"
    race = boss.get("race") or "ไม่ระบุ"
    boss_channel = boss.get("channel") or "ไม่ระบุ"
    boss_server = boss.get("server") or DEFAULT_BOSS_SERVER
    return f"Server: {boss_server} | ช่องเกิด: {boss_channel} | ธาตุ: {element} | เผ่า: {race}"


def normalize_boss_server(server: str | None) -> str:
    value = (server or DEFAULT_BOSS_SERVER).strip()
    for supported_server in SUPPORTED_BOSS_SERVERS:
        if value.lower() == supported_server.lower():
            return supported_server
    raise ValueError("invalid server")


def boss_server_choices() -> list[discord.app_commands.Choice[str]]:
    return [
        discord.app_commands.Choice(name=server, value=server)
        for server in SUPPORTED_BOSS_SERVERS
    ]


def parse_boss_status_args(args: tuple[str, ...]) -> tuple[str, int]:
    boss_server = DEFAULT_BOSS_SERVER
    page = 1

    for arg in args:
        value = arg.strip()
        if not value:
            continue
        if value.isdigit():
            page = max(1, int(value))
            continue
        boss_server = normalize_boss_server(value)

    return boss_server, page


def infer_boss_server_from_channel(channel: object) -> str:
    channel_id = getattr(channel, "id", None)
    for server, alert_channel_id in SERVER_ALERT_CHANNEL_IDS.items():
        if channel_id and alert_channel_id and int(channel_id) == int(alert_channel_id):
            return server
    return DEFAULT_BOSS_SERVER


def channel_env_key_for_server(server: str | None) -> str:
    return f"DISCORD_{normalize_boss_server(server).upper()}_CHANNEL_ID"


def server_id_suffix(server: str | None) -> str:
    return normalize_boss_server(server).lower()


def boss_channel_label(boss: dict) -> str:
    boss_channel = str(boss.get("channel") or "").strip()
    if not boss_channel or boss_channel == "ไม่ระบุ":
        return ""

    compact_channel = re.sub(r"[\s_-]+", "", boss_channel).upper()
    if compact_channel.startswith("CH"):
        return compact_channel

    return f"CH{compact_channel}"


def boss_server_channel_label(boss: dict) -> str:
    channel_label = boss_channel_label(boss)
    boss_server = boss.get("server") or DEFAULT_BOSS_SERVER
    if channel_label:
        return f"{boss_server} {channel_label}"
    return str(boss_server)


def boss_description(boss: dict) -> str:
    return (
        f"{boss_detail_line(boss)} | "
        f"คูลดาวน์ {boss['cooldown_minutes']} นาที"
    )


def boss_spawn_value(boss: dict) -> str:
    if not boss.get("next_spawn"):
        return "ยังไม่มีเวลาที่บันทึกไว้"

    try:
        spawn_time = parse_time(str(boss["next_spawn"]))
    except (TypeError, ValueError):
        return "เวลาเกิดที่บันทึกไว้ผิดรูปแบบ"

    return f"{discord_time(spawn_time)} ({discord_time(spawn_time, 'R')})"


def boss_status_table_spawn_value(boss: dict) -> str:
    if not boss.get("next_spawn"):
        return "ยังไม่มีเวลาที่บันทึกไว้"

    try:
        spawn_time = parse_time(str(boss["next_spawn"]))
    except (TypeError, ValueError):
        return "เวลาผิดรูปแบบ"

    return spawn_time.astimezone(LOCAL_TIMEZONE).strftime("%d/%m %H:%M")


def upcoming_boss_spawns(limit: int = 5, server: str | None = None) -> list[tuple[str, dict, datetime, timedelta]]:
    now = utc_now()
    upcoming: list[tuple[str, dict, datetime, timedelta]] = []
    boss_server = normalize_boss_server(server) if server is not None else None

    for boss_id, boss in boss_data.items():
        if boss_server is not None and normalize_boss_server(str(boss.get("server") or DEFAULT_BOSS_SERVER)) != boss_server:
            continue

        if not boss.get("next_spawn"):
            continue

        try:
            cooldown_minutes = int(boss.get("cooldown_minutes", 0))
            spawn_time = parse_time(str(boss["next_spawn"]))
        except (TypeError, ValueError):
            continue

        remaining_time = spawn_time - now
        if cooldown_minutes == 0 or remaining_time.total_seconds() < 0:
            continue

        upcoming.append((boss_id, boss, spawn_time, remaining_time))

    upcoming.sort(key=lambda item: item[3])
    return upcoming[:limit]


def active_boss_alert_items(server: str | None = None) -> list[tuple[str, dict, datetime, timedelta, bool]]:
    now = utc_now()
    boss_server = normalize_boss_server(server)
    active_items: list[tuple[str, dict, datetime, timedelta, bool]] = []

    for boss_id, boss in sorted_boss_items():
        if not boss.get("alert_sent") or not boss.get("next_spawn"):
            continue

        try:
            current_server = normalize_boss_server(str(boss.get("server") or DEFAULT_BOSS_SERVER))
            spawn_time = parse_time(str(boss["next_spawn"]))
            cooldown_minutes = int(boss.get("cooldown_minutes", 0))
        except (TypeError, ValueError):
            continue

        if current_server != boss_server or cooldown_minutes == 0:
            continue

        age = now - spawn_time
        if age.total_seconds() < 0:
            continue

        is_expired = age >= timedelta(minutes=UNRECORDED_BOSS_RESET_MINUTES)
        active_items.append((boss_id, boss, spawn_time, age, is_expired))

    active_items.sort(key=lambda item: item[2])
    return active_items


def active_boss_alert_page(server: str | None, page: int) -> tuple[list[tuple[str, dict, datetime, timedelta, bool]], int, int]:
    items = active_boss_alert_items(server)
    total_pages = max(1, (len(items) + ACTIVE_ALERTS_PER_PAGE - 1) // ACTIVE_ALERTS_PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * ACTIVE_ALERTS_PER_PAGE
    return items[start : start + ACTIVE_ALERTS_PER_PAGE], page, total_pages


def user_group_boss_ids(user_id: int | str) -> list[str]:
    groups = load_boss_groups()
    return groups.get(str(user_id), [])


def add_bosses_to_user_group(user_id: int | str, selected_boss_ids: list[str]) -> tuple[list[str], list[str], list[str]]:
    groups = load_boss_groups()
    key = str(user_id)
    current_group = groups.get(key, [])
    added: list[str] = []
    skipped: list[str] = []
    invalid: list[str] = []

    for boss_id in selected_boss_ids:
        if boss_id not in boss_data:
            invalid.append(boss_id)
            continue
        if boss_id in current_group:
            skipped.append(boss_id)
            continue
        if len(current_group) >= MAX_GROUP_BOSSES:
            skipped.append(boss_id)
            continue
        current_group.append(boss_id)
        added.append(boss_id)

    groups[key] = current_group[:MAX_GROUP_BOSSES]
    save_boss_groups(groups)
    return added, skipped, invalid


def clear_user_group(user_id: int | str) -> int:
    key = str(user_id)
    removed_count = database.clear_boss_group(key)
    logging.info("Cleared boss group for user %s; removed %s rows.", key, removed_count)
    return removed_count


def replace_user_group(user_id: int | str, boss_ids: list[str]) -> list[str]:
    valid_boss_ids = [boss_id for boss_id in boss_ids if boss_id in boss_data][:MAX_GROUP_BOSSES]
    groups = load_boss_groups()
    groups[str(user_id)] = valid_boss_ids
    save_boss_groups(groups)
    return valid_boss_ids


def boss_group_lines(boss_ids: list[str]) -> list[str]:
    lines: list[str] = []
    for index, boss_id in enumerate(boss_ids, start=1):
        boss = boss_data.get(boss_id)
        if not boss:
            lines.append(f"{index}. ไม่พบบอส `{boss_id}`")
            continue
        lines.append(
            f"{index}. {boss['name']} | {boss_server_channel_label(boss)} | "
            f"{boss_spawn_value(boss)}"
        )
    return lines


def build_user_group_message(user: discord.abc.User) -> str:
    boss_ids = user_group_boss_ids(user.id)
    if not boss_ids:
        return (
            f"**กลุ่มเป้าหมายล่าบอสของ {user.display_name}**\n"
            f"ยังไม่มีบอสในกลุ่ม เลือกได้สูงสุด {MAX_GROUP_BOSSES} ตัว"
        )

    return (
        f"**กลุ่มเป้าหมายล่าบอสของ {user.display_name}** "
        f"({len(boss_ids)}/{MAX_GROUP_BOSSES})\n"
        + "\n".join(boss_group_lines(boss_ids))
    )


def member_display(user: discord.abc.User) -> str:
    display_name = getattr(user, "display_name", user.name)
    return f"{user.mention} ({display_name})"


async def send_private_command_result(
    ctx: commands.Context,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
) -> bool:
    try:
        await ctx.author.send(content=content, embed=embed, view=view)
    except discord.HTTPException:
        await ctx.reply(
            "บอทส่งผลลัพธ์ทาง DM ให้ไม่ได้ กรุณาเปิดรับข้อความส่วนตัวจากสมาชิกในเซิร์ฟเวอร์นี้",
            delete_after=12,
        )
        return False

    try:
        await ctx.message.add_reaction("✅")
    except discord.HTTPException:
        pass
    return True


async def send_private_command_error(ctx: commands.Context, content: str) -> None:
    try:
        await ctx.author.send(content)
        try:
            await ctx.message.add_reaction("⚠️")
        except discord.HTTPException:
            pass
    except discord.HTTPException:
        await ctx.reply(
            "บอทส่งข้อความส่วนตัวให้ไม่ได้ กรุณาเปิดรับ DM จากสมาชิกในเซิร์ฟเวอร์นี้",
            delete_after=12,
        )


def build_kill_record_embed(
    boss: dict,
    next_spawn: datetime,
    recorded_by: discord.abc.User | None = None,
) -> discord.Embed:
    recorder_line = ""
    if recorded_by is not None:
        recorder_line = f"\nผู้บันทึก: {member_display(recorded_by)}"

    embed = discord.Embed(
        title="บันทึกเวลาบอสตายแล้ว",
        description=(
            f"**{boss['name']}** จะเกิดรอบถัดไป\n"
            f"{boss_detail_line(boss)}\n"
            f"เวลา: {discord_time(next_spawn)} ({discord_time(next_spawn, 'R')})"
            f"{recorder_line}"
        ),
        color=0xE74C3C,
    )
    embed.set_footer(text=f"คูลดาวน์ {boss['cooldown_minutes']} นาที")
    return embed


def build_kill_record_text(
    boss: dict,
    next_spawn: datetime,
    recorded_by: discord.abc.User | None = None,
) -> str:
    recorder_line = ""
    if recorded_by is not None:
        recorder_line = f"\nผู้บันทึก: {member_display(recorded_by)}"

    return (
        "บันทึกเวลาบอสตายแล้ว\n"
        f"{boss['name']} จะเกิดรอบถัดไป\n"
        f"{boss_detail_line(boss)}\n"
        f"เวลา: {discord_time(next_spawn)} ({discord_time(next_spawn, 'R')})"
        f"{recorder_line}\n"
        f"คูลดาวน์ {boss['cooldown_minutes']} นาที"
    )


def parse_countdown_minutes(value: str) -> int:
    countdown_text = value.strip()
    if ":" in countdown_text:
        parts = countdown_text.split(":")
        if len(parts) != 2:
            raise ValueError("invalid format")
        hours_text, minutes_text = parts
    elif countdown_text.isdigit() and 1 <= len(countdown_text) <= 4:
        padded_text = countdown_text.zfill(4)
        hours_text = padded_text[:-2]
        minutes_text = padded_text[-2:]
    else:
        raise ValueError("invalid format")

    if not hours_text.isdigit() or not minutes_text.isdigit():
        raise ValueError("invalid number")

    hours = int(hours_text)
    minutes = int(minutes_text)
    if hours < 0 or minutes < 0 or minutes > 59:
        raise ValueError("invalid range")

    total_minutes = hours * 60 + minutes
    if total_minutes <= 0:
        raise ValueError("zero cooldown")

    return total_minutes


def format_countdown_minutes(total_minutes: int) -> str:
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"


def parse_add_boss_details(details: str) -> tuple[str, str, str, str, str]:
    parts = [part.strip() for part in details.split("|")]
    if len(parts) == 5 and all(parts):
        server, channel, element, race, name = parts
        return normalize_boss_server(server), channel, element, race, name
    if len(parts) == 4 and all(parts):
        channel, element, race, name = parts
        return DEFAULT_BOSS_SERVER, channel, element, race, name
    if len(parts) == 3 and all(parts):
        element, race, name = parts
        return DEFAULT_BOSS_SERVER, "ไม่ระบุ", element, race, name

    return DEFAULT_BOSS_SERVER, "ไม่ระบุ", "ไม่ระบุ", "ไม่ระบุ", details.strip()


def parse_edit_boss_details(details: str) -> dict[str, Any]:
    parts = [part.strip() for part in details.split("|")]
    keys = ["name", "server", "channel", "element", "race", "cooldown_minutes"]
    updates: dict[str, Any] = {}

    for key, value in zip(keys, parts):
        if not value or value == "-":
            continue
        if key == "server":
            updates[key] = normalize_boss_server(value)
        elif key == "cooldown_minutes":
            updates[key] = int(value)
        else:
            updates[key] = value

    return updates


def channel_id_suffix(channel: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "", channel.strip().lower())
    if not value:
        return "unknown"
    if value.startswith("ch"):
        return value
    return f"ch{value}"


def boss_id_base(boss_id: str) -> str:
    value = SERVER_SUFFIX_PATTERN.sub("", boss_id.strip())
    return CHANNEL_SUFFIX_PATTERN.sub("", value)


def boss_id_for_channel(boss_id: str, channel: str, server: str | None = None) -> str:
    return f"{boss_id_base(boss_id)}_{channel_id_suffix(channel)}_{server_id_suffix(server)}"


def resolve_boss_id(boss_id: str) -> str:
    if boss_id in boss_data:
        return boss_id

    for stored_boss_id, boss in boss_data.items():
        if boss.get("legacy_id") == boss_id:
            return stored_boss_id

    raise KeyError(boss_id)


def record_boss_kill(boss_id: str, cooldown_minutes: int | None = None) -> tuple[dict, datetime]:
    reload_boss_data()
    boss_id = resolve_boss_id(boss_id)

    boss = boss_data[boss_id]
    if cooldown_minutes is not None:
        boss["cooldown_minutes"] = cooldown_minutes

    next_spawn = utc_now() + timedelta(minutes=int(boss["cooldown_minutes"]))
    boss["next_spawn"] = format_time(next_spawn)
    boss["alert_sent"] = False
    boss.pop("alert_message_id", None)
    save_boss_record(boss_id, boss)
    return boss, next_spawn


def update_boss_cooldown(boss_id: str, cooldown_minutes: int) -> tuple[dict, datetime]:
    reload_boss_data()
    boss_id = resolve_boss_id(boss_id)

    boss = boss_data[boss_id]
    boss["cooldown_minutes"] = cooldown_minutes
    next_spawn = utc_now() + timedelta(minutes=cooldown_minutes)
    boss["next_spawn"] = format_time(next_spawn)
    boss["alert_sent"] = False
    boss.pop("alert_message_id", None)
    save_boss_record(boss_id, boss)
    return boss, next_spawn


def reset_boss_cooldowns(boss_id: str | None = None) -> list[tuple[str, dict]]:
    reload_boss_data()
    if boss_id is not None:
        boss_id = resolve_boss_id(boss_id)
        boss_data[boss_id]["cooldown_minutes"] = 0
        boss_data[boss_id].pop("alert_message_id", None)
        save_boss_record(boss_id, boss_data[boss_id])
        return [(boss_id, boss_data[boss_id])]

    for boss in boss_data.values():
        boss["cooldown_minutes"] = 0
        boss.pop("alert_message_id", None)

    save_boss_data(boss_data)
    return boss_items()


def replace_boss_id_in_groups(old_boss_id: str, new_boss_id: str | None = None) -> None:
    groups = load_boss_groups()
    changed = False

    for user_id, boss_ids in list(groups.items()):
        updated_boss_ids: list[str] = []
        for group_boss_id in boss_ids:
            if group_boss_id != old_boss_id:
                updated_boss_ids.append(group_boss_id)
                continue
            if new_boss_id is not None and new_boss_id not in updated_boss_ids:
                updated_boss_ids.append(new_boss_id)
            changed = True
        groups[user_id] = updated_boss_ids[:MAX_GROUP_BOSSES]

    if changed:
        save_boss_groups(groups)


def update_boss_details(boss_id: str, updates: dict[str, Any]) -> tuple[str, str, dict]:
    reload_boss_data()
    old_boss_id = resolve_boss_id(boss_id)
    boss = dict(boss_data[old_boss_id])

    editable_fields = ["name", "server", "channel", "element", "race", "cooldown_minutes"]
    for field in editable_fields:
        if field not in updates or updates[field] is None:
            continue
        if field == "server":
            boss[field] = normalize_boss_server(str(updates[field]))
        elif field == "cooldown_minutes":
            boss[field] = int(updates[field])
        else:
            boss[field] = str(updates[field]).strip()

    new_boss_id = boss_id_for_channel(
        old_boss_id,
        str(boss.get("channel") or "ไม่ระบุ"),
        str(boss.get("server") or DEFAULT_BOSS_SERVER),
    )

    if new_boss_id != old_boss_id and new_boss_id in boss_data:
        raise ValueError(f"`{new_boss_id}` มีอยู่แล้ว")

    if new_boss_id != old_boss_id:
        boss.setdefault("legacy_id", old_boss_id)
        boss["alert_sent"] = False
        boss.pop("alert_message_id", None)
        del boss_data[old_boss_id]
        boss_data[new_boss_id] = boss
        delete_boss_record(old_boss_id)
        save_boss_record(new_boss_id, boss)
        replace_boss_id_in_groups(old_boss_id, new_boss_id)
    else:
        boss_data[old_boss_id] = boss
        save_boss_record(old_boss_id, boss)

    return old_boss_id, new_boss_id, boss


def delete_boss_data(boss_id: str) -> tuple[str, dict]:
    reload_boss_data()
    stored_boss_id = resolve_boss_id(boss_id)
    boss = boss_data.pop(stored_boss_id)
    delete_boss_record(stored_boss_id)
    replace_boss_id_in_groups(stored_boss_id, None)
    return stored_boss_id, boss


def current_boss_spawn_time(boss_id: str) -> datetime | None:
    reload_boss_data()
    try:
        boss_id = resolve_boss_id(boss_id)
    except KeyError:
        return None

    boss = boss_data.get(boss_id)
    if not boss or not boss.get("next_spawn"):
        return None

    try:
        return parse_time(str(boss["next_spawn"]))
    except (TypeError, ValueError):
        return None


def is_same_spawn_time(first: datetime | None, second: datetime | None) -> bool:
    if first is None or second is None:
        return False
    return int(first.timestamp()) == int(second.timestamp())


def get_alert_message_id(message: discord.Message | None) -> int:
    if message is None:
        return 0
    return message.id


def get_alert_record_lock(message_id: int) -> asyncio.Lock:
    if message_id not in alert_record_locks:
        alert_record_locks[message_id] = asyncio.Lock()
    return alert_record_locks[message_id]


async def disable_alert_killed_button(
    message: discord.Message | None,
    boss_id: str,
) -> None:
    if message is None:
        return

    try:
        await message.edit(view=AlertBossKilledView(boss_id, disabled=True))
    except discord.HTTPException as error:
        logging.exception("Could not disable alert killed button on message %s: %s", message.id, error)


async def disable_alert_killed_button_by_id(
    channel: discord.abc.Messageable,
    boss_id: str,
    message_id: int | str | None,
    view: discord.ui.View | None = None,
) -> None:
    if not message_id or not hasattr(channel, "fetch_message"):
        return

    try:
        message = await channel.fetch_message(int(message_id))
    except (TypeError, ValueError, discord.HTTPException) as error:
        logging.exception("Could not fetch alert message %s for %s: %s", message_id, boss_id, error)
        return

    try:
        await message.edit(view=view or AlertBossKilledView(boss_id, disabled=True))
    except discord.HTTPException as error:
        logging.exception("Could not disable alert killed button on message %s: %s", message.id, error)


async def delete_message_after_delay(message: discord.Message, delay_seconds: int) -> None:
    await asyncio.sleep(delay_seconds)

    try:
        await message.delete()
        logging.info("Deleted alert message %s after %s seconds.", message.id, delay_seconds)
    except discord.NotFound:
        logging.info("Alert message %s was already deleted.", message.id)
    except discord.HTTPException as error:
        logging.exception("Could not delete alert message %s: %s", message.id, error)


def schedule_alert_message_delete(message: discord.Message | None) -> None:
    if message is None:
        return

    delete_at = utc_now() + timedelta(seconds=ALERT_MESSAGE_DELETE_DELAY_SECONDS)
    queue = load_message_delete_queue()
    message_id = str(message.id)
    queue = [item for item in queue if str(item.get("message_id")) != message_id]
    queue.append(
        {
            "channel_id": str(message.channel.id),
            "message_id": message_id,
            "delete_at": format_time(delete_at),
        }
    )
    save_message_delete_queue(queue)
    logging.info("Queued alert message %s for deletion at %s.", message.id, format_time(delete_at))


def append_alert_delete_notice(content: str) -> str:
    if ALERT_MESSAGE_DELETE_NOTICE in content:
        return content

    return f"{content}\n\n{ALERT_MESSAGE_DELETE_NOTICE}"


async def disable_expired_alert_cooldown_button(
    channel: discord.abc.Messageable | None,
    boss_id: str,
    message_id: int | str | None,
) -> None:
    if not channel or not message_id or not hasattr(channel, "fetch_message"):
        return

    try:
        message = await channel.fetch_message(int(message_id))
    except (TypeError, ValueError, discord.HTTPException) as error:
        logging.exception("Could not fetch expired alert message %s for %s: %s", message_id, boss_id, error)
        return

    try:
        await message.edit(
            content=append_alert_delete_notice(message.content),
            view=ExpiredAlertCooldownView(boss_id, cooldown_disabled=True),
        )
        schedule_alert_message_delete(message)
    except discord.HTTPException as error:
        logging.exception("Could not disable alert cooldown button on message %s: %s", message.id, error)


async def disable_missing_cooldown_button(
    channel: discord.abc.Messageable | None,
    boss_id: str,
    message_id: int | str | None,
) -> None:
    if not channel or not message_id or not hasattr(channel, "fetch_message"):
        return

    try:
        message = await channel.fetch_message(int(message_id))
    except (TypeError, ValueError, discord.HTTPException) as error:
        logging.exception("Could not fetch missing cooldown message %s for %s: %s", message_id, boss_id, error)
        return

    try:
        await message.edit(
            content=append_alert_delete_notice(message.content),
            view=SetCooldownView(boss_id, disabled=True),
        )
        schedule_alert_message_delete(message)
    except discord.HTTPException as error:
        logging.exception("Could not disable missing cooldown button on message %s: %s", message.id, error)


async def update_alert_message_after_kill(
    message: discord.Message | None,
    boss_id: str,
    boss: dict,
    next_spawn: datetime,
    recorded_by: discord.abc.User,
) -> None:
    if message is None:
        return

    base_content = message.content
    kill_record_text = build_kill_record_text(boss, next_spawn, recorded_by)
    new_content = append_alert_delete_notice(f"{base_content}\n\n{kill_record_text}")

    try:
        await message.edit(
            content=new_content,
            view=AlertBossKilledView(boss_id, disabled=True),
        )
        schedule_alert_message_delete(message)
    except discord.HTTPException as error:
        logging.exception("Could not update alert message %s after kill: %s", message.id, error)


async def get_alert_channel(server: str | None = None) -> discord.abc.Messageable | None:
    boss_server = normalize_boss_server(server)
    channel_id = SERVER_ALERT_CHANNEL_IDS.get(boss_server) or CHANNEL_ID
    if not channel_id:
        logging.warning("Alert channel for server %s is not set; boss alerts are disabled.", boss_server)
        return None

    channel = bot.get_channel(channel_id)
    if channel is not None:
        logging.info("Using cached alert channel %s for server %s (%s).", channel_id, boss_server, type(channel).__name__)
        return channel

    try:
        channel = await bot.fetch_channel(channel_id)
    except discord.HTTPException as error:
        logging.exception("Could not fetch alert channel %s for server %s: %s", channel_id, boss_server, error)
        return None

    if not isinstance(channel, discord.abc.Messageable):
        logging.warning("Alert channel %s for server %s is not messageable.", channel_id, boss_server)
        return None

    logging.info("Fetched alert channel %s for server %s (%s).", channel_id, boss_server, type(channel).__name__)
    return channel


async def send_group_spawn_alerts(boss_id: str, boss: dict, spawn_time: datetime) -> None:
    groups = load_boss_groups()
    if not groups:
        return

    channel_label = boss_channel_label(boss)
    title_parts = [boss["name"]]
    if channel_label:
        title_parts.append(channel_label)
    title = " ".join(title_parts)

    for user_id, boss_ids in groups.items():
        if boss_id not in boss_ids:
            continue

        try:
            user = bot.get_user(int(user_id)) or await bot.fetch_user(int(user_id))
            await user.send(
                f"⚠️ **{title} เริ่มเข้าเฟส** ({discord_time(spawn_time, 'R')})\n"
                f"เวลาเกิด: {discord_time(spawn_time, 'F')}\n"
                f"{boss_detail_line(boss)}"
            )
            logging.info("Sent group DM spawn alert for %s to user %s.", boss_id, user_id)
        except (ValueError, discord.Forbidden, discord.HTTPException) as error:
            logging.exception("Could not send group DM spawn alert for %s to user %s: %s", boss_id, user_id, error)


@tasks.loop(seconds=30)
async def check_message_delete_queue() -> None:
    queue = load_message_delete_queue()
    if not queue:
        return

    now = utc_now()
    remaining_queue: list[dict] = []

    for item in queue:
        try:
            delete_at = parse_time(str(item["delete_at"]))
            channel_id = int(item["channel_id"])
            message_id = int(item["message_id"])
        except (KeyError, TypeError, ValueError):
            continue

        if now < delete_at:
            remaining_queue.append(item)
            continue

        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except discord.HTTPException as error:
                logging.exception("Could not fetch channel %s for queued delete: %s", channel_id, error)
                remaining_queue.append(item)
                continue

        if not hasattr(channel, "fetch_message"):
            logging.warning("Channel %s does not support fetch_message for queued delete.", channel_id)
            continue

        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
            logging.info("Deleted queued alert message %s.", message_id)
        except discord.NotFound:
            logging.info("Queued alert message %s was already deleted.", message_id)
        except discord.HTTPException as error:
            logging.exception("Could not delete queued alert message %s: %s", message_id, error)
            remaining_queue.append(item)

    save_message_delete_queue(remaining_queue)


@check_message_delete_queue.before_loop
async def before_check_message_delete_queue() -> None:
    await bot.wait_until_ready()


async def set_alert_channel_value(channel: discord.abc.Messageable, server: str | None = None) -> None:
    global CHANNEL_ID
    if server is None:
        CHANNEL_ID = channel.id
        save_env_value("DISCORD_CHANNEL_ID", str(channel.id))
        return

    boss_server = normalize_boss_server(server)
    SERVER_ALERT_CHANNEL_IDS[boss_server] = channel.id
    save_env_value(channel_env_key_for_server(boss_server), str(channel.id))


class BossSelect(discord.ui.Select):
    def __init__(self, page: int):
        self.page = normalize_page(page)
        options = [
            discord.SelectOption(
                label=boss["name"][:100],
                value=boss_id,
                description=boss_description(boss)[:100],
            )
            for boss_id, boss in page_slice(self.page)
        ]

        if not options:
            options = [
                discord.SelectOption(
                    label="ยังไม่มีบอส",
                    value="__empty__",
                    description="เพิ่มบอสด้วยคำสั่ง add_boss ก่อน",
                )
            ]

        super().__init__(
            placeholder="เลือกบอสที่ตายแล้ว",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"boss_select:{self.page}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        boss_id = self.values[0]
        if boss_id == "__empty__":
            await interaction.response.send_message("ยังไม่มีบอสในระบบ", ephemeral=True)
            return

        try:
            boss, next_spawn = record_boss_kill(boss_id)
        except KeyError:
            await interaction.response.send_message(
                "ไม่พบบอสนี้แล้ว ลองใช้ `!setup_boss` เพื่อสร้างแผงเลือกใหม่",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=build_kill_record_embed(boss, next_spawn, interaction.user)
        )


class BossPagerButton(discord.ui.Button):
    def __init__(self, page: int, direction: int):
        self.page = page
        self.direction = direction
        label = "ก่อนหน้า" if direction < 0 else "ถัดไป"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"boss_page:{page}:{direction}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        next_page = normalize_page(self.page + self.direction)
        await interaction.response.edit_message(
            embed=build_setup_embed(next_page),
            view=BossView(next_page),
        )


class BossStatusButton(discord.ui.Button):
    def __init__(self, page: int):
        self.page = normalize_page(page)
        super().__init__(
            label="สถานะ",
            style=discord.ButtonStyle.primary,
            custom_id=f"boss_status_button:{self.page}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        reload_boss_data()
        await send_boss_status_page_interaction(interaction, self.page, ephemeral=True)


class AlertBossKilledButton(discord.ui.Button):
    def __init__(self, boss_id: str, disabled: bool = False):
        self.boss_id = boss_id
        super().__init__(
            label="บันทึกแล้ว" if disabled else "บันทึกเวลา",
            style=discord.ButtonStyle.danger,
            custom_id=f"alert_boss_killed:{boss_id}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        message = interaction.message
        message_id = get_alert_message_id(message)
        if message_id in processed_alert_message_ids:
            await disable_alert_killed_button(message, self.boss_id)
            await interaction.response.send_message(
                "ข้อความแจ้งเตือนนี้ถูกบันทึกว่าบอสตายแล้วไปก่อนหน้านี้แล้ว",
                ephemeral=True,
            )
            return

        alert_spawn_time = current_boss_spawn_time(self.boss_id)
        if alert_spawn_time is None:
            await interaction.response.send_message(
                "ไม่พบเวลาเกิดเดิมของ alert นี้ อาจมีการบันทึกหรือเคลียร์ข้อมูลไปแล้ว",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            AlertBossKilledModal(self.boss_id, message, alert_spawn_time)
        )


class AlertActiveBossAlertsButton(discord.ui.Button):
    def __init__(self, boss_id: str):
        self.boss_id = boss_id
        super().__init__(
            label="ดูรายการที่กำลังรอบันทึกทั้งหมด",
            style=discord.ButtonStyle.secondary,
            custom_id=f"alert_active_boss_alerts:{boss_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        reload_boss_data()
        try:
            stored_boss_id = resolve_boss_id(self.boss_id)
            boss = boss_data.get(stored_boss_id)
        except KeyError:
            boss = None

        try:
            boss_server = normalize_boss_server(str(boss.get("server") if boss else DEFAULT_BOSS_SERVER))
        except ValueError:
            boss_server = DEFAULT_BOSS_SERVER

        await interaction.response.send_message(
            embed=build_active_boss_alerts_embed(boss_server, 1),
            view=ActiveBossAlertsView(boss_server, 1),
            ephemeral=True,
        )


class AlertBossKilledModal(discord.ui.Modal):
    def __init__(
        self,
        boss_id: str,
        alert_message: discord.Message | None,
        alert_spawn_time: datetime | None,
    ):
        super().__init__(title="บันทึกเวลาบอสตายแล้ว")
        self.boss_id = boss_id
        self.alert_message = alert_message
        self.alert_spawn_time = alert_spawn_time
        self.countdown = discord.ui.TextInput(
            label="เวลาเค้าดาวน์ HHMM หรือ HH:MM",
            placeholder="เช่น 0030 = 30 นาที, 01:35 = 1 ชั่วโมง 35 นาที",
            default="01:00",
            min_length=1,
            max_length=5,
            required=True,
        )
        self.add_item(self.countdown)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            cooldown_minutes = parse_countdown_minutes(str(self.countdown.value))
        except ValueError:
            await interaction.response.send_message(
                "รูปแบบเวลาไม่ถูกต้อง กรุณาใส่เป็น `HHMM` หรือ `HH:MM` เช่น `0030`, `01:35`",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        message_id = get_alert_message_id(self.alert_message)
        async with get_alert_record_lock(message_id):
            if message_id in processed_alert_message_ids:
                await disable_alert_killed_button(self.alert_message, self.boss_id)
                await interaction.followup.send(
                    "ข้อความแจ้งเตือนนี้ถูกบันทึกว่าบอสตายแล้วไปก่อนหน้านี้แล้ว",
                    ephemeral=True,
                )
                return

            current_spawn_time = current_boss_spawn_time(self.boss_id)
            if not is_same_spawn_time(self.alert_spawn_time, current_spawn_time):
                processed_alert_message_ids.add(message_id)
                await disable_alert_killed_button(self.alert_message, self.boss_id)
                await interaction.followup.send(
                    "มีคนบันทึกบอสจากข้อความแจ้งเตือนนี้ไปแล้ว จึงไม่บันทึกซ้ำ",
                    ephemeral=True,
                )
                return

            try:
                boss, next_spawn = record_boss_kill(self.boss_id, cooldown_minutes)
            except KeyError:
                await interaction.followup.send(
                    "ไม่พบบอสนี้แล้ว ลองใช้ `/setup_boss` เพื่อสร้างแผงเลือกใหม่",
                    ephemeral=True,
                )
                return

            processed_alert_message_ids.add(message_id)

        await update_alert_message_after_kill(
            self.alert_message,
            self.boss_id,
            boss,
            next_spawn,
            interaction.user,
        )


class AlertBossKilledView(discord.ui.View):
    def __init__(self, boss_id: str, disabled: bool = False):
        super().__init__(timeout=None)
        self.add_item(AlertBossKilledButton(boss_id, disabled))
        self.add_item(AlertActiveBossAlertsButton(boss_id))


class ActiveBossKilledModal(discord.ui.Modal):
    def __init__(self, boss_id: str, alert_spawn_time: datetime):
        super().__init__(title="บันทึกเวลาคูลดาวน์")
        self.boss_id = boss_id
        self.alert_spawn_time = alert_spawn_time
        self.countdown = discord.ui.TextInput(
            label="เวลาคูลดาวน์ HHMM หรือ HH:MM",
            placeholder="เช่น 0030 = 30 นาที, 01:35 = 1 ชั่วโมง 35 นาที",
            default="01:00",
            min_length=1,
            max_length=5,
            required=True,
        )
        self.add_item(self.countdown)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            cooldown_minutes = parse_countdown_minutes(str(self.countdown.value))
        except ValueError:
            await interaction.response.send_message(
                "รูปแบบเวลาไม่ถูกต้อง กรุณาใส่เป็น `HHMM` หรือ `HH:MM` เช่น `0030`, `01:35`",
                ephemeral=True,
            )
            return

        current_spawn_time = current_boss_spawn_time(self.boss_id)
        if not is_same_spawn_time(self.alert_spawn_time, current_spawn_time):
            await interaction.response.send_message(
                "รายการนี้ถูกบันทึกหรือเปลี่ยนรอบไปแล้ว กรุณาเปิด `/active_boss_alerts` ใหม่อีกครั้ง",
                ephemeral=True,
            )
            return

        try:
            boss, next_spawn = record_boss_kill(self.boss_id, cooldown_minutes)
        except KeyError:
            await interaction.response.send_message("ไม่พบบอสนี้แล้ว", ephemeral=True)
            return

        await interaction.response.send_message(
            embed=build_kill_record_embed(boss, next_spawn, interaction.user),
            ephemeral=True,
        )


class ActiveBossAlertSelect(discord.ui.Select):
    def __init__(self, server: str, page: int = 1):
        self.server = normalize_boss_server(server)
        self.page = page
        items, _, _ = active_boss_alert_page(self.server, page)
        options = []

        for boss_id, boss, spawn_time, age, is_expired in items:
            status = "เกิน 45 นาที" if is_expired else "รอบันทึก"
            age_minutes = max(0, int(age.total_seconds() // 60))
            channel_label = boss_channel_label(boss)
            label_parts = [boss["name"]]
            if channel_label:
                label_parts.append(channel_label)
            options.append(
                discord.SelectOption(
                    label=" ".join(label_parts)[:100],
                    value=boss_id,
                    description=f"{status} | เกิดเมื่อ {age_minutes} นาที | {boss_id}"[:100],
                )
            )

        if not options:
            options = [
                discord.SelectOption(
                    label="ไม่มีบอสที่รอบันทึก",
                    value="__empty__",
                    description="ตอนนี้ไม่มี active alert ใน server นี้",
                )
            ]

        super().__init__(
            placeholder="เลือกบอสที่ตายแล้วเพื่อบันทึกคูลดาวน์",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"active_boss_alert_select:{self.server}:{self.page}",
            disabled=options[0].value == "__empty__",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        boss_id = self.values[0]
        if boss_id == "__empty__":
            await interaction.response.send_message("ไม่มีบอสที่กำลังรอบันทึก", ephemeral=True)
            return

        spawn_time = current_boss_spawn_time(boss_id)
        if spawn_time is None:
            await interaction.response.send_message(
                "รายการนี้ไม่มีเวลาเกิดแล้ว กรุณาเปิด `/active_boss_alerts` ใหม่อีกครั้ง",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(ActiveBossKilledModal(boss_id, spawn_time))


class ActiveBossAlertsView(discord.ui.View):
    def __init__(self, server: str, page: int = 1):
        super().__init__(timeout=300)
        self.add_item(ActiveBossAlertSelect(server, page))


class AlertSetCooldownButton(discord.ui.Button):
    def __init__(self, boss_id: str, disabled: bool = False):
        self.boss_id = boss_id
        super().__init__(
            label="ตั้งค่าคูลดาวน์",
            style=discord.ButtonStyle.primary,
            custom_id=f"alert_set_cooldown:{boss_id}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        alert_message_id = interaction.message.id if interaction.message else None
        await interaction.response.send_modal(SetCooldownModal(self.boss_id, alert_message_id=alert_message_id))


class AlertSetCooldownView(discord.ui.View):
    def __init__(self, boss_id: str):
        super().__init__(timeout=None)
        self.add_item(AlertSetCooldownButton(boss_id))
        self.add_item(AlertActiveBossAlertsButton(boss_id))


class ExpiredAlertCooldownView(discord.ui.View):
    def __init__(self, boss_id: str, cooldown_disabled: bool = False):
        super().__init__(timeout=None)
        self.add_item(AlertBossKilledButton(boss_id, disabled=True))
        self.add_item(AlertSetCooldownButton(boss_id, disabled=cooldown_disabled))
        self.add_item(AlertActiveBossAlertsButton(boss_id))


class SetCooldownButton(discord.ui.Button):
    def __init__(self, boss_id: str, disabled: bool = False):
        self.boss_id = boss_id
        super().__init__(
            label="ตั้งค่าคูลดาวน์",
            style=discord.ButtonStyle.primary,
            custom_id=f"set_cooldown:{boss_id}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cooldown_message_id = interaction.message.id if interaction.message else None
        await interaction.response.send_modal(
            SetCooldownModal(self.boss_id, cooldown_message_id=cooldown_message_id)
        )


class SetCooldownModal(discord.ui.Modal):
    def __init__(
        self,
        boss_id: str,
        alert_message_id: int | None = None,
        cooldown_message_id: int | None = None,
    ):
        super().__init__(title="ตั้งค่าคูลดาวน์บอส")
        self.boss_id = boss_id
        self.alert_message_id = alert_message_id
        self.cooldown_message_id = cooldown_message_id
        self.countdown = discord.ui.TextInput(
            label="เวลาคูลดาวน์ HHMM หรือ HH:MM",
            placeholder="เช่น 0030 = 30 นาที, 01:35 = 1 ชั่วโมง 35 นาที",
            default="01:00",
            min_length=1,
            max_length=5,
            required=True,
        )
        self.add_item(self.countdown)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            cooldown_minutes = parse_countdown_minutes(str(self.countdown.value))
        except ValueError:
            await interaction.response.send_message(
                "รูปแบบเวลาไม่ถูกต้อง กรุณาใส่เป็น `HHMM` หรือ `HH:MM` เช่น `0030`, `01:35`",
                ephemeral=True,
            )
            return

        try:
            boss, next_spawn = update_boss_cooldown(self.boss_id, cooldown_minutes)
        except KeyError:
            await interaction.response.send_message(
                "ไม่พบบอสนี้แล้ว ลองใช้ `/setup_boss` เพื่อสร้างแผงเลือกใหม่",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ ตั้งค่าคูลดาวน์ของ **{boss['name']}** เป็น `{format_countdown_minutes(cooldown_minutes)}` "
            f"({cooldown_minutes} นาที) แล้ว\n"
            f"เวลาเกิดรอบถัดไป: {discord_time(next_spawn)} ({discord_time(next_spawn, 'R')})\n"
            f"ผู้บันทึก: {member_display(interaction.user)}",
            ephemeral=True,
        )
        await disable_expired_alert_cooldown_button(
            interaction.channel,
            self.boss_id,
            self.alert_message_id,
        )
        await disable_missing_cooldown_button(
            interaction.channel,
            self.boss_id,
            self.cooldown_message_id,
        )


class SetCooldownView(discord.ui.View):
    def __init__(self, boss_id: str, disabled: bool = False):
        super().__init__(timeout=None)
        self.add_item(SetCooldownButton(boss_id, disabled=disabled))


def boss_needs_cooldown(boss: dict) -> bool:
    try:
        return int(boss.get("cooldown_minutes", 0)) == 0
    except (TypeError, ValueError):
        return True


def build_single_boss_status_text(boss_id: str, boss: dict) -> str:
    return (
        f"{boss['name']} ({boss_id})\n"
        f"{boss_spawn_value(boss)}\n"
        f"{boss_detail_line(boss)}\n"
        f"คูลดาวน์ {boss['cooldown_minutes']} นาที"
    )


def table_text_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.combining(char) or unicodedata.category(char) in {"Mn", "Me"}:
            continue
        if unicodedata.east_asian_width(char) in {"F", "W"}:
            width += 2
        else:
            width += 1
    return width


def truncate_table_text(text: str, width: int) -> str:
    if table_text_width(text) <= width:
        return text

    suffix = "..."
    suffix_width = table_text_width(suffix)
    target_width = max(0, width - suffix_width)
    result = ""
    current_width = 0

    for char in text:
        char_width = 0
        if not unicodedata.combining(char) and unicodedata.category(char) not in {"Mn", "Me"}:
            char_width = 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        if current_width + char_width > target_width:
            break
        result += char
        current_width += char_width

    return result + suffix


def fit_table_cell(value: object, width: int, align: str = "left") -> str:
    text = str(value)
    text = truncate_table_text(text, width)
    padding = max(0, width - table_text_width(text))
    if align == "center":
        left_padding = padding // 2
        right_padding = padding - left_padding
        return " " * left_padding + text + " " * right_padding
    return text + " " * padding


def status_table_header_lines() -> list[str]:
    widths = [width for _, width in STATUS_TABLE_COLUMNS]
    separator = "|" + "|".join("-" * width for width in widths) + "|"
    header = "|" + "|".join(
        fit_table_cell(title, width, "center") for title, width in STATUS_TABLE_COLUMNS
    ) + "|"
    return [separator, header, separator]


def build_status_table_row_lines(boss_id: str, boss: dict) -> list[str]:
    widths = [width for _, width in STATUS_TABLE_COLUMNS]
    separator = "|" + "|".join("-" * width for width in widths) + "|"
    row_values = [
        f"{boss['name']} ({boss_id})",
        boss_status_table_spawn_value(boss),
        f"Server: {boss.get('server') or DEFAULT_BOSS_SERVER}",
        f"ช่อง: {boss.get('channel') or 'ไม่ระบุ'}",
        f"ธาตุ: {boss.get('element') or 'ไม่ระบุ'}",
        f"เผ่า: {boss.get('race') or 'ไม่ระบุ'}",
        f"คูลดาวน์ {boss['cooldown_minutes']} นาที",
    ]
    row = (
        "|"
        + "|".join(
            fit_table_cell(value, width)
            for value, width in zip(row_values, widths)
        )
        + "|"
    )
    return [row, separator]


def build_status_table_message(page: int, server: str | None = DEFAULT_BOSS_SERVER) -> str:
    boss_server = normalize_boss_server(server)
    page = normalize_status_page(page, boss_server)
    items = status_page_slice(page, boss_server)
    if not items:
        return f"ไม่มีบอสของ Server {boss_server} ในหน้านี้"

    lines = status_table_header_lines()

    for boss_id, boss in items:
        lines.extend(build_status_table_row_lines(boss_id, boss))

    lines.append(f"Server {boss_server} | หน้า {page + 1}/{status_total_pages(boss_server)} | {len(boss_items_for_server(boss_server))} บอส")
    message = "```text\n" + "\n".join(lines) + "\n```"
    if len(message) > STATUS_TABLE_LIMIT:
        logging.warning("Boss status table is %s characters and may exceed Discord limits.", len(message))

    return message


async def send_boss_status_page(ctx: commands.Context, page: int, server: str | None = DEFAULT_BOSS_SERVER) -> None:
    await send_private_command_result(ctx, build_status_table_message(page, server))


async def send_boss_status_page_interaction(
    interaction: discord.Interaction,
    page: int,
    server: str | None = DEFAULT_BOSS_SERVER,
    ephemeral: bool = False,
) -> None:
    await interaction.response.send_message(build_status_table_message(page, server), ephemeral=ephemeral)


class BossView(discord.ui.View):
    def __init__(self, page: int = 0):
        super().__init__(timeout=None)
        self.page = normalize_page(page)
        self.add_item(BossSelect(self.page))
        self.add_item(BossStatusButton(self.page))
        if total_pages() > 1:
            if self.page > 0:
                self.add_item(BossPagerButton(self.page, -1))
            if self.page < total_pages() - 1:
                self.add_item(BossPagerButton(self.page, 1))


class BossGroupSelect(discord.ui.Select):
    def __init__(self, page: int, server: str = DEFAULT_BOSS_SERVER):
        self.server = normalize_boss_server(server)
        self.page = normalize_group_page(page, self.server)
        options = [
            discord.SelectOption(
                label=f"{boss['name']} {boss_server_channel_label(boss)}"[:100],
                value=boss_id,
                description=boss_description(boss)[:100],
            )
            for boss_id, boss in group_page_slice(self.page, self.server)
        ]

        if not options:
            options = [
                discord.SelectOption(
                    label="ยังไม่มีบอส",
                    value="__empty__",
                    description="เพิ่มบอสด้วยคำสั่ง add_boss ก่อน",
                )
            ]

        super().__init__(
            placeholder=f"เลือกบอส {self.server} เข้ากลุ่มเป้าหมาย สูงสุด {MAX_GROUP_BOSSES} ตัว",
            min_values=1,
            max_values=min(MAX_GROUP_BOSSES, len(options)),
            options=options,
            custom_id=f"boss_group_select:{server_id_suffix(self.server)}:{self.page}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if "__empty__" in self.values:
            await interaction.response.send_message("ยังไม่มีบอสในระบบ", ephemeral=True)
            return

        reload_boss_data()
        added, skipped, invalid = add_bosses_to_user_group(interaction.user.id, list(self.values))
        lines = []
        if added:
            lines.append("เพิ่มเข้ากลุ่มแล้ว:")
            lines.extend(f"- {boss_data[boss_id]['name']} ({boss_id})" for boss_id in added)
        if skipped:
            lines.append("ข้ามรายการที่ซ้ำหรือกลุ่มเต็ม:")
            lines.extend(f"- {boss_id}" for boss_id in skipped)
        if invalid:
            lines.append("ไม่พบรายการ:")
            lines.extend(f"- {boss_id}" for boss_id in invalid)
        lines.append("")
        lines.append(build_user_group_message(interaction.user))
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


class BossGroupPagerButton(discord.ui.Button):
    def __init__(self, page: int, direction: int, server: str = DEFAULT_BOSS_SERVER):
        self.server = normalize_boss_server(server)
        self.page = page
        self.direction = direction
        target_page = normalize_group_page(page + direction, self.server)
        label = f"หน้า {target_page + 1}/{group_total_pages(self.server)}"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"boss_group_page:{server_id_suffix(self.server)}:{page}:{direction}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        next_page = normalize_group_page(self.page + self.direction, self.server)
        await interaction.response.edit_message(
            embed=build_boss_group_embed(next_page, self.server),
            view=BossGroupView(next_page, self.server),
        )


class BossGroupShowButton(discord.ui.Button):
    def __init__(self, page: int, server: str = DEFAULT_BOSS_SERVER):
        self.server = normalize_boss_server(server)
        self.page = normalize_group_page(page, self.server)
        super().__init__(
            label="ดูกลุ่มของฉัน",
            style=discord.ButtonStyle.primary,
            custom_id=f"boss_group_show:{server_id_suffix(self.server)}:{self.page}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        reload_boss_data()
        await interaction.response.send_message(build_user_group_message(interaction.user), ephemeral=True)


class BossGroupClearButton(discord.ui.Button):
    def __init__(self, page: int, server: str = DEFAULT_BOSS_SERVER):
        self.server = normalize_boss_server(server)
        self.page = normalize_group_page(page, self.server)
        super().__init__(
            label="ล้างกลุ่ม",
            style=discord.ButtonStyle.danger,
            custom_id=f"boss_group_clear:{server_id_suffix(self.server)}:{self.page}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        removed_count = clear_user_group(interaction.user.id)
        await interaction.response.send_message(
            f"ล้างกลุ่มเป้าหมายแล้ว {removed_count} รายการ",
            ephemeral=True,
        )


class BossGroupUpcomingButton(discord.ui.Button):
    def __init__(self, page: int, server: str = DEFAULT_BOSS_SERVER):
        self.server = normalize_boss_server(server)
        self.page = normalize_group_page(page, self.server)
        super().__init__(
            label="เพิ่ม 5 ตัวใกล้เกิด",
            style=discord.ButtonStyle.success,
            custom_id=f"boss_group_upcoming:{server_id_suffix(self.server)}:{self.page}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        reload_boss_data()
        upcoming = upcoming_boss_spawns(MAX_GROUP_BOSSES, self.server)
        if not upcoming:
            all_boss_ids = [boss_id for boss_id, _boss in boss_items_for_server(self.server)]
            if not all_boss_ids:
                await interaction.response.send_message(
                    f"ยังไม่มีรายชื่อบอสในระบบของ Server {self.server}",
                    ephemeral=True,
                )
                return

            selected_boss_ids = random.sample(
                all_boss_ids,
                k=min(MAX_GROUP_BOSSES, len(all_boss_ids)),
            )
            replace_user_group(interaction.user.id, selected_boss_ids)
            lines = [f"ยังไม่พบบอส Server {self.server} ที่มีเวลาเกิดรอบถัดไป ระบบสุ่มบอสให้แทน:"]
            for index, boss_id in enumerate(selected_boss_ids, start=1):
                boss = boss_data[boss_id]
                lines.append(f"{index}. {boss['name']} | {boss_server_channel_label(boss)}")
            lines.append("")
            lines.append(build_user_group_message(interaction.user))
            await interaction.response.send_message("\n".join(lines), ephemeral=True)
            return

        selected_boss_ids = [boss_id for boss_id, _boss, _spawn_time, _remaining_time in upcoming]
        replace_user_group(interaction.user.id, selected_boss_ids)
        lines = [f"ตั้งกลุ่มเป็นบอส Server {self.server} ที่ใกล้เกิดที่สุดแล้ว:"]
        for index, (boss_id, boss, spawn_time, _remaining_time) in enumerate(upcoming, start=1):
            lines.append(
                f"{index}. {boss['name']} | {boss_server_channel_label(boss)} | "
                f"เวลาเกิด: {discord_time(spawn_time)} ({discord_time(spawn_time, 'R')})"
            )
        lines.append("")
        lines.append(build_user_group_message(interaction.user))
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


class BossGroupView(discord.ui.View):
    def __init__(self, page: int = 0, server: str = DEFAULT_BOSS_SERVER):
        super().__init__(timeout=None)
        self.server = normalize_boss_server(server)
        self.page = normalize_group_page(page, self.server)
        self.add_item(BossGroupSelect(self.page, self.server))
        self.add_item(BossGroupUpcomingButton(self.page, self.server))
        self.add_item(BossGroupShowButton(self.page, self.server))
        self.add_item(BossGroupClearButton(self.page, self.server))
        if group_total_pages(self.server) > 1:
            if self.page > 0:
                self.add_item(BossGroupPagerButton(self.page, -1, self.server))
            if self.page < group_total_pages(self.server) - 1:
                self.add_item(BossGroupPagerButton(self.page, 1, self.server))


def build_setup_embed(page: int = 0) -> discord.Embed:
    page = normalize_page(page)
    embed = discord.Embed(
        title="แผงบันทึกเวลาบอส",
        description=(
            "เมื่อบอสตาย ให้เลือกชื่อบอสจากเมนูด้านล่าง "
            "แล้วระบบจะคำนวณเวลาเกิดรอบถัดไปให้อัตโนมัติ"
        ),
        color=0xE74C3C,
    )
    embed.set_footer(text=f"ปักหมุดข้อความนี้ไว้ให้สมาชิกกดใช้งานได้ตลอด | หน้า {page + 1}/{total_pages()} | {len(boss_data)} บอส")
    return embed


def build_boss_group_embed(page: int = 0, server: str = DEFAULT_BOSS_SERVER) -> discord.Embed:
    boss_server = normalize_boss_server(server)
    page = normalize_group_page(page, boss_server)
    server_boss_count = len(boss_items_for_server(boss_server))
    embed = discord.Embed(
        title=f"จัดกลุ่มเป้าหมายล่าบอส Server {boss_server}",
        description=(
            f"เลือกบอสเข้ากลุ่มส่วนตัวได้สูงสุด {MAX_GROUP_BOSSES} ตัว "
            "เมื่อบอสในกลุ่มเริ่มเข้าเฟส ระบบจะส่ง DM แจ้งเตือนไปหาเจ้าของกลุ่ม\n"
            "กดปุ่มเพิ่ม 5 ตัวใกล้เกิด เพื่อเลือกบอสที่ใกล้เวลาเกิดที่สุด หรือสุ่มให้ถ้ายังไม่มีเวลาบอสเกิด\n"
            "รายชื่อบอสในเมนูจะแสดงเฉพาะ server นี้ และแบ่งหน้าเพราะ Discord แสดงได้สูงสุด 25 รายการต่อเมนู"
        ),
        color=0x9B59B6,
    )
    embed.set_footer(text=f"หน้า {page + 1}/{group_total_pages(boss_server)} | {server_boss_count} บอส")
    return embed


def build_status_embed(page: int = 0) -> discord.Embed:
    page = normalize_page(page)
    embed = discord.Embed(
        title="สถานะเวลาบอส",
        description="รายการเวลาเกิดรอบถัดไปของบอสที่กำลังติดตาม",
        color=0x3498DB,
    )

    for boss_id, boss in page_slice(page):
        embed.add_field(
            name=f"{boss['name']} ({boss_id})",
            value=(
                f"{boss_spawn_value(boss)}\n"
                f"{boss_detail_line(boss)}\n"
                f"คูลดาวน์ {boss['cooldown_minutes']} นาที"
            ),
            inline=False,
        )

    embed.set_footer(text=f"หน้า {page + 1}/{total_pages()} | {len(boss_data)} บอส")
    return embed


def build_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="คู่มือใช้งาน BossHunter",
        description="คำสั่งและปุ่มสำหรับบันทึกเวลาเกิดบอส",
        color=0x2ECC71,
    )
    embed.add_field(
        name="สมาชิกทั่วไป",
        value=(
            "`/setup_boss server:Austeja` หรือ `server:Jurate` สร้างแผงจัดกลุ่มเป้าหมายล่าบอสเฉพาะ server\n"
            "`/boss_killed` เปิดเมนูเลือกบอสที่ตายแล้ว\n"
            "`/boss_status server:Austeja` ดูสถานะบอสของ server ที่เลือก\n"
            "`/boss_status server:Jurate page:2` ดูสถานะหน้าที่ 2 ของ server นั้น\n"
            "`/boss_info boss_id:boss_lv80_ch1_austeja` ดูรายละเอียดบอสจาก boss_id\n"
            "`/active_boss_alerts server:Austeja` ดูบอสที่เกิดแล้วและกำลังรอบันทึกเวลา\n"
            "`/find_boss_spawn` ดูบอสใกล้เกิดที่สุด 5 ตัว\n"
            "`/boss_killed_id boss_id:boss_lv80_ch1` บันทึกบอสตายด้วย id"
        ),
        inline=False,
    )
    embed.add_field(
        name="ตั้งค่าคูลดาวน์",
        value=(
            "`/set_boss_cooldown boss_id:boss_lv80_ch1 countdown:0030` ตั้ง cooldown เป็น 30 นาที\n"
            "`/reset_cooldown_boss` ตั้ง cooldown ของบอสทุกตัวเป็น 0\n"
            "`/reset_cooldown_boss boss_id:boss_lv90_ch1` ตั้ง cooldown ของบอสที่ระบุเป็น 0"
        ),
        inline=False,
    )
    embed.add_field(
        name="แอดมิน",
        value=(
            "`/add_boss boss_id:boss_lv100 cooldown_minutes:480 name:ชื่อบอส server:Austeja channel:CH-1 element:ไฟ race:มังกร` เพิ่มบอส\n"
            "ระบบจะบันทึก id เป็น `boss_lv100_ch1_austeja` หรือ `boss_lv100_ch1_jurate` ตาม server และ channel\n"
            "`/edit_boss boss_id:boss_lv100_ch1_austeja name:ชื่อใหม่ element:ไฟ` แก้ไขข้อมูลบอส\n"
            "`/delete_boss boss_id:boss_lv100_ch1_austeja` ลบข้อมูลบอส\n"
            "`/clear_boss boss_id:boss_lv80_ch1` ล้างเวลาเกิดที่บันทึกไว้\n"
            "`/set_cooldown_boss server:Jurate page:2` ส่งรายการบอสที่ยังไม่มีคูลดาวน์ไปยังห้องนี้ ครั้งละ 10 รายการ\n"
            "`/set_alert_channel server:Austeja channel:#boss-austeja` ตั้งห้องแจ้งเตือนของ server\n"
            "`/boss_killed` ส่งแผงบันทึกเวลาบอสตาย"
        ),
        inline=False,
    )
    embed.add_field(
        name="รูปแบบเวลา",
        value="ใช้ `HHMM` หรือ `HH:MM` เช่น `0030`, `00:06`, `01:35`, `02:00`",
        inline=False,
    )
    embed.set_footer(text="แนะนำให้ใช้ Slash Commands แบบ / เพื่อความง่าย")
    return embed


def build_find_boss_spawn_message() -> str:
    upcoming = upcoming_boss_spawns(5)
    if not upcoming:
        return "ยังไม่พบบอสที่มีเวลาเกิดรอบถัดไป"

    lines = ["**บอสที่ใกล้เวลาเกิดที่สุด 5 ตัว**", "เรียงจากตัวที่ใกล้เกิดที่สุดก่อน"]
    for index, (boss_id, boss, spawn_time, _remaining_time) in enumerate(upcoming, start=1):
        channel_label = boss_server_channel_label(boss)
        lines.append(
            f"{index}. {boss['name']} | {channel_label} | "
            f"เวลาเกิด: {discord_time(spawn_time)} ({discord_time(spawn_time, 'R')})"
        )

    return "\n".join(lines)


def build_spawn_alert_embed(boss: dict, spawn_time: datetime) -> discord.Embed:
    channel_label = boss_channel_label(boss)
    title_parts = [boss["name"]]
    if channel_label:
        title_parts.append(channel_label)

    embed = discord.Embed(
        title="🟢 รอบันทึกเวลา",
        description=f"**{' '.join(title_parts)} เริ่มเข้าเฟส** ({discord_time(spawn_time, 'R')})",
        color=0x2ECC71,
    )
    embed.add_field(name="เวลาเกิด", value=discord_time(spawn_time, "F"), inline=False)
    embed.add_field(name="Server", value=str(boss.get("server") or DEFAULT_BOSS_SERVER), inline=True)
    embed.add_field(name="Channel", value=str(boss.get("channel") or "ไม่ระบุ"), inline=True)
    embed.add_field(name="คูลดาวน์", value=f"{boss.get('cooldown_minutes', 0)} นาที", inline=True)
    embed.add_field(name="ธาตุ", value=str(boss.get("element") or "ไม่ระบุ"), inline=True)
    embed.add_field(name="เผ่า", value=str(boss.get("race") or "ไม่ระบุ"), inline=True)
    embed.add_field(name="วิธีใช้งาน", value="กดปุ่ม **บันทึกเวลา** หลังบอสตาย หรือกด **ดูรายการที่กำลังรอบันทึกทั้งหมด** เพื่อเลือกจากแผงรวม", inline=False)
    return embed


def build_active_boss_alerts_embed(server: str | None = DEFAULT_BOSS_SERVER, page: int = 1) -> discord.Embed:
    boss_server = normalize_boss_server(server)
    items, page, total_pages = active_boss_alert_page(boss_server, page)
    total_count = len(active_boss_alert_items(boss_server))
    embed = discord.Embed(
        title=f"📌 บอสที่เกิดแล้วและรอบันทึก | {boss_server}",
        color=0xF1C40F if items else 0x95A5A6,
    )

    if not items:
        embed.description = "ตอนนี้ไม่มีบอสที่กำลังรอบันทึกเวลา"
        embed.set_footer(text=f"หน้า {page}/{total_pages} | 0 รายการ")
        return embed

    lines = []
    for index, (boss_id, boss, spawn_time, age, is_expired) in enumerate(items, start=(page - 1) * ACTIVE_ALERTS_PER_PAGE + 1):
        status = "🟠 เกิน 45 นาที" if is_expired else "🟢 รอบันทึก"
        channel_label = boss_channel_label(boss)
        channel_text = f" | {channel_label}" if channel_label else ""
        age_minutes = max(0, int(age.total_seconds() // 60))
        lines.append(
            f"{index}. {status} **{boss['name']}**{channel_text} | เกิดเมื่อ {age_minutes} นาที | `{boss_id}`"
        )

    embed.description = "\n".join(lines)
    embed.add_field(
        name="การใช้งาน",
        value="เลือกบอสจากเมนูด้านล่างเพื่อกรอกเวลาคูลดาวน์หลังบอสตาย",
        inline=False,
    )
    embed.set_footer(text=f"หน้า {page}/{total_pages} | {total_count} รายการ")
    return embed


def build_boss_info_embed(boss_id: str, boss: dict) -> discord.Embed:
    embed = discord.Embed(
        title=boss["name"],
        description=f"boss_id: `{boss_id}`",
        color=0x3498DB,
    )
    embed.add_field(name="Server", value=str(boss.get("server") or DEFAULT_BOSS_SERVER), inline=True)
    embed.add_field(name="Channel", value=str(boss.get("channel") or "ไม่ระบุ"), inline=True)
    embed.add_field(name="คูลดาวน์", value=f"{boss.get('cooldown_minutes', 0)} นาที", inline=True)
    embed.add_field(name="ธาตุ", value=str(boss.get("element") or "ไม่ระบุ"), inline=True)
    embed.add_field(name="เผ่า", value=str(boss.get("race") or "ไม่ระบุ"), inline=True)
    embed.add_field(name="สถานะแจ้งเตือน", value="ส่งแล้ว" if boss.get("alert_sent") else "ยังไม่ส่ง", inline=True)
    embed.add_field(name="เวลาเกิดรอบถัดไป", value=boss_spawn_value(boss), inline=False)

    if boss.get("legacy_id"):
        embed.add_field(name="legacy_id", value=f"`{boss['legacy_id']}`", inline=False)
    if boss.get("alert_message_id"):
        embed.add_field(name="alert_message_id", value=f"`{boss['alert_message_id']}`", inline=False)

    return embed


def build_set_boss_cooldown_message(
    boss: dict,
    countdown: str,
    cooldown_minutes: int,
    next_spawn: datetime,
) -> str:
    return (
        f"ตั้งค่าคูลดาวน์ของ **{boss['name']}** เป็น `{countdown}` "
        f"({cooldown_minutes} นาที) แล้ว\n"
        f"เวลาเกิดรอบถัดไป: {discord_time(next_spawn)} ({discord_time(next_spawn, 'R')})"
    )


def missing_cooldown_boss_items(server: str | None = None) -> list[tuple[str, dict]]:
    boss_server = normalize_boss_server(server)
    items: list[tuple[str, dict]] = []

    for boss_id, boss in sorted_boss_items():
        try:
            cooldown_minutes = int(boss.get("cooldown_minutes", 0))
        except (TypeError, ValueError):
            cooldown_minutes = 0

        if cooldown_minutes != 0:
            continue

        try:
            current_server = normalize_boss_server(str(boss.get("server") or DEFAULT_BOSS_SERVER))
        except ValueError:
            current_server = DEFAULT_BOSS_SERVER

        if current_server == boss_server:
            items.append((boss_id, boss))

    return items


async def send_missing_cooldown_alert(
    channel: discord.abc.Messageable,
    boss_id: str,
    boss: dict,
) -> None:
    await channel.send(
        f"⏱️ **ต้องตั้งค่าคูลดาวน์บอส**\n"
        f"บอส: **{boss['name']}** (`{boss_id}`)\n"
        f"{boss_detail_line(boss)}\n"
        f"ตอนนี้ `cooldown_minutes` เป็น `0` กรุณากดปุ่มด้านล่างเพื่อใส่เวลาคูลดาวน์รูปแบบ `HHMM` หรือ `HH:MM`",
        view=SetCooldownView(boss_id),
    )


async def send_missing_cooldown_alerts(
    channel: discord.abc.Messageable,
    server: str | None = None,
    *,
    page: int | None = None,
    per_page: int = MISSING_COOLDOWN_ALERTS_PER_PAGE,
) -> tuple[int, int]:
    boss_server = normalize_boss_server(server)
    items = missing_cooldown_boss_items(boss_server)
    total_count = len(items)
    if page is not None:
        page = max(1, page)
        start = (page - 1) * per_page
        items = items[start : start + per_page]

    sent_count = 0

    for boss_id, boss in items:
        try:
            await send_missing_cooldown_alert(channel, boss_id, boss)
        except discord.HTTPException as error:
            logging.exception("Could not send missing cooldown alert for %s: %s", boss_id, error)
            continue

        sent_count += 1
        logging.info("Sent missing cooldown alert for %s (%s).", boss_id, boss["name"])

    return sent_count, total_count


def build_add_boss_message(stored_boss_id: str, boss: dict, setup_command: str) -> str:
    return (
        f"เพิ่มบอส **{boss['name']}** แล้ว\n"
        f"boss_id: `{stored_boss_id}`\n"
        f"{boss_detail_line(boss)}\n"
        f"ใช้ `{setup_command}` เพื่อสร้างแผงเลือกบอสใหม่"
    )


def build_edit_boss_message(old_boss_id: str, new_boss_id: str, boss: dict) -> str:
    moved_line = ""
    if old_boss_id != new_boss_id:
        moved_line = f"\nเปลี่ยน boss_id: `{old_boss_id}` -> `{new_boss_id}`"

    return (
        f"แก้ไขข้อมูลบอส **{boss['name']}** แล้ว\n"
        f"boss_id: `{new_boss_id}`"
        f"{moved_line}\n"
        f"{boss_detail_line(boss)}\n"
        f"คูลดาวน์ {boss['cooldown_minutes']} นาที"
    )


def build_delete_boss_message(stored_boss_id: str, boss: dict) -> str:
    return (
        f"ลบบอส **{boss['name']}** แล้ว\n"
        f"boss_id: `{stored_boss_id}`\n"
        f"{boss_detail_line(boss)}"
    )


@tasks.loop(seconds=30)
async def check_boss_spawns() -> None:
    async with boss_spawn_check_lock:
        reload_boss_data()

        now = utc_now()
        changed = False
        for boss_id, boss in boss_data.items():
            if not boss.get("next_spawn"):
                continue

            try:
                spawn_time = parse_time(str(boss["next_spawn"]))
            except (TypeError, ValueError):
                continue

            try:
                boss_server = normalize_boss_server(str(boss.get("server") or DEFAULT_BOSS_SERVER))
            except ValueError:
                boss_server = DEFAULT_BOSS_SERVER

            if boss.get("alert_sent"):
                reset_deadline = spawn_time + timedelta(minutes=UNRECORDED_BOSS_RESET_MINUTES)
                try:
                    cooldown_minutes = int(boss.get("cooldown_minutes", 0))
                except (TypeError, ValueError):
                    cooldown_minutes = 0

                if cooldown_minutes != 0 and now >= reset_deadline:
                    alert_message_id = boss.get("alert_message_id")
                    channel = await get_alert_channel(boss_server)
                    if channel is not None:
                        await disable_alert_killed_button_by_id(
                            channel,
                            boss_id,
                            alert_message_id,
                            view=ExpiredAlertCooldownView(boss_id),
                        )
                    if alert_message_id:
                        try:
                            processed_alert_message_ids.add(int(alert_message_id))
                        except (TypeError, ValueError):
                            pass
                    boss["cooldown_minutes"] = 0
                    boss["next_spawn"] = None
                    boss["alert_sent"] = False
                    boss.pop("alert_message_id", None)
                    changed = True
                    logging.info(
                        "Reset cooldown for unrecorded boss %s (%s) after %s minutes.",
                        boss_id,
                        boss["name"],
                        UNRECORDED_BOSS_RESET_MINUTES,
                    )
                continue

            grace_deadline = spawn_time + timedelta(minutes=ALERT_GRACE_MINUTES)
            if spawn_time <= now <= grace_deadline:
                channel = await get_alert_channel(boss_server)
                if channel is None:
                    continue

                next_spawn_value = str(boss["next_spawn"])
                if not database.claim_boss_spawn_alert(boss_id, next_spawn_value):
                    logging.info("Skipped duplicate spawn alert claim for %s.", boss_id)
                    continue

                boss["alert_sent"] = True
                boss.pop("alert_message_id", None)
                try:
                    alert_message = await channel.send(
                        embed=build_spawn_alert_embed(boss, spawn_time),
                        view=AlertBossKilledView(boss_id),
                    )
                except discord.HTTPException as error:
                    database.rollback_boss_spawn_alert_claim(boss_id, next_spawn_value)
                    boss["alert_sent"] = False
                    logging.exception("Could not send alert for %s: %s", boss_id, error)
                    continue

                database.complete_boss_spawn_alert(boss_id, next_spawn_value, alert_message.id)
                boss["alert_message_id"] = alert_message.id
                await send_group_spawn_alerts(boss_id, boss, spawn_time)
                logging.info("Sent spawn alert for %s (%s).", boss_id, boss["name"])

        if changed:
            save_boss_data(boss_data)


@tasks.loop(hours=MISSING_COOLDOWN_REMINDER_HOURS)
async def check_missing_cooldowns() -> None:
    reload_boss_data()

    for boss_server in SUPPORTED_BOSS_SERVERS:
        channel = await get_alert_channel(boss_server)
        if channel is None:
            continue

        await send_missing_cooldown_alerts(channel, boss_server)


@check_boss_spawns.before_loop
async def before_check_boss_spawns() -> None:
    await bot.wait_until_ready()


@check_missing_cooldowns.before_loop
async def before_check_missing_cooldowns() -> None:
    await bot.wait_until_ready()
    await asyncio.sleep(MISSING_COOLDOWN_REMINDER_HOURS * 60 * 60)


@bot.event
async def on_ready() -> None:
    global slash_commands_synced, persistent_views_registered
    reload_boss_data()
    if not persistent_views_registered:
        for server in SUPPORTED_BOSS_SERVERS:
            for page in range(group_total_pages(server)):
                bot.add_view(BossGroupView(page, server))
        for page in range(total_pages()):
            bot.add_view(BossView(page))
        for boss_id in boss_data:
            bot.add_view(AlertBossKilledView(boss_id))
            bot.add_view(AlertSetCooldownView(boss_id))
            bot.add_view(SetCooldownView(boss_id))
            legacy_id = boss_data[boss_id].get("legacy_id")
            if legacy_id:
                bot.add_view(AlertBossKilledView(str(legacy_id)))
                bot.add_view(AlertSetCooldownView(str(legacy_id)))
                bot.add_view(SetCooldownView(str(legacy_id)))
        persistent_views_registered = True
        logging.info("Persistent views registered for %s bosses.", len(boss_data))
    if not check_boss_spawns.is_running():
        check_boss_spawns.start()
    if ENABLE_MISSING_COOLDOWN_REMINDERS and not check_missing_cooldowns.is_running():
        check_missing_cooldowns.start()
    elif not ENABLE_MISSING_COOLDOWN_REMINDERS:
        logging.info("Missing cooldown reminder loop is disabled.")
    if not check_message_delete_queue.is_running():
        check_message_delete_queue.start()
    if not slash_commands_synced:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            logging.info("Slash commands synced to guild %s.", GUILD_ID)
        else:
            await bot.tree.sync()
            logging.info("Slash commands synced globally.")
        slash_commands_synced = True
    logging.info("Boss Hunter bot is online as %s.", bot.user)


@bot.command(name="setup_boss")
async def setup_boss(ctx: commands.Context, server: str = DEFAULT_BOSS_SERVER) -> None:
    reload_boss_data()
    try:
        boss_server = normalize_boss_server(server)
    except ValueError:
        await send_private_command_error(ctx, f"server ต้องเป็น {', '.join(SUPPORTED_BOSS_SERVERS)}")
        return

    try:
        await ctx.author.send(embed=build_boss_group_embed(server=boss_server), view=BossGroupView(server=boss_server))
    except discord.HTTPException:
        await send_private_command_error(ctx, "บอทส่งแผงจัดกลุ่มทาง DM ให้ไม่ได้ กรุณาเปิดรับข้อความส่วนตัวจากสมาชิกในเซิร์ฟเวอร์นี้")
        return

    try:
        await ctx.message.add_reaction("✅")
    except discord.HTTPException:
        pass


@bot.tree.command(name="setup_boss", description="ส่งแผงจัดกลุ่มเป้าหมายล่าบอส")
@discord.app_commands.choices(server=boss_server_choices())
async def setup_boss_slash(
    interaction: discord.Interaction,
    server: str = DEFAULT_BOSS_SERVER,
) -> None:
    reload_boss_data()
    try:
        boss_server = normalize_boss_server(server)
    except ValueError:
        await interaction.response.send_message(f"server ต้องเป็น {', '.join(SUPPORTED_BOSS_SERVERS)}", ephemeral=True)
        return

    await interaction.response.send_message(
        embed=build_boss_group_embed(server=boss_server),
        view=BossGroupView(server=boss_server),
        ephemeral=True,
    )


@bot.command(name="help")
async def help_command(ctx: commands.Context) -> None:
    await send_private_command_result(ctx, embed=build_help_embed())


@bot.tree.command(name="help", description="แสดงคู่มือการใช้งานคำสั่ง BossHunter")
async def help_slash(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(embed=build_help_embed(), ephemeral=True)


@bot.command(name="boss_status")
async def boss_status(ctx: commands.Context, *args: str) -> None:
    reload_boss_data()
    try:
        boss_server, page = parse_boss_status_args(args)
    except ValueError:
        await send_private_command_error(ctx, f"server ต้องเป็น {', '.join(SUPPORTED_BOSS_SERVERS)}")
        return

    await send_private_command_result(ctx, build_status_table_message(page - 1, boss_server))


@bot.command(name="boss_info")
async def boss_info(ctx: commands.Context, boss_id: str) -> None:
    reload_boss_data()
    try:
        stored_boss_id = resolve_boss_id(boss_id)
    except KeyError:
        await send_private_command_error(ctx, f"ไม่พบ `{boss_id}` ใช้ `!boss_status` เพื่อดูรายการบอส")
        return

    await send_private_command_result(ctx, embed=build_boss_info_embed(stored_boss_id, boss_data[stored_boss_id]))


@bot.tree.command(name="boss_info", description="ดูรายละเอียดบอสจาก boss_id")
async def boss_info_slash(interaction: discord.Interaction, boss_id: str) -> None:
    reload_boss_data()
    try:
        stored_boss_id = resolve_boss_id(boss_id)
    except KeyError:
        await interaction.response.send_message(f"ไม่พบ `{boss_id}` ใช้ `/boss_status` เพื่อดูรายการบอส", ephemeral=True)
        return

    await interaction.response.send_message(
        embed=build_boss_info_embed(stored_boss_id, boss_data[stored_boss_id]),
        ephemeral=True,
    )


@bot.command(name="active_boss_alerts")
async def active_boss_alerts(
    ctx: commands.Context,
    server: str = DEFAULT_BOSS_SERVER,
    page: int = 1,
) -> None:
    reload_boss_data()
    try:
        boss_server = normalize_boss_server(server)
    except ValueError:
        await send_private_command_error(ctx, f"server ต้องเป็น {', '.join(SUPPORTED_BOSS_SERVERS)}")
        return

    embed = build_active_boss_alerts_embed(boss_server, page)
    view = ActiveBossAlertsView(boss_server, page)
    await send_private_command_result(ctx, embed=embed, view=view)


@bot.tree.command(name="active_boss_alerts", description="ดูบอสที่เกิดแล้วและกำลังรอบันทึกเวลา")
@discord.app_commands.choices(server=boss_server_choices())
async def active_boss_alerts_slash(
    interaction: discord.Interaction,
    server: str = DEFAULT_BOSS_SERVER,
    page: int = 1,
) -> None:
    reload_boss_data()
    try:
        boss_server = normalize_boss_server(server)
    except ValueError:
        await interaction.response.send_message(f"server ต้องเป็น {', '.join(SUPPORTED_BOSS_SERVERS)}", ephemeral=True)
        return

    await interaction.response.send_message(
        embed=build_active_boss_alerts_embed(boss_server, page),
        view=ActiveBossAlertsView(boss_server, page),
        ephemeral=True,
    )


@bot.command(name="set_alert_channel")
@commands.has_permissions(manage_guild=True)
async def set_alert_channel(
    ctx: commands.Context,
    server: str = DEFAULT_BOSS_SERVER,
    channel: Optional[discord.TextChannel] = None,
) -> None:
    try:
        boss_server = normalize_boss_server(server)
    except ValueError:
        if ctx.message.channel_mentions:
            boss_server = DEFAULT_BOSS_SERVER
        else:
            await send_private_command_error(ctx, f"server ต้องเป็น {', '.join(SUPPORTED_BOSS_SERVERS)}")
            return

    target = channel or (ctx.message.channel_mentions[0] if ctx.message.channel_mentions else ctx.channel)
    try:
        await set_alert_channel_value(target, boss_server)
    except discord.HTTPException as error:
        await send_private_command_error(ctx, f"บอทส่งข้อความใน channel นี้ไม่ได้: `{error}`")
        return

    await send_private_command_result(ctx, f"ตั้งค่าห้องแจ้งเตือนบอส Server {boss_server} เป็น {target.mention} แล้ว")


@bot.tree.command(name="set_alert_channel", description="ตั้ง channel สำหรับแจ้งเตือนบอสเกิด")
@discord.app_commands.default_permissions(manage_guild=True)
@discord.app_commands.choices(server=boss_server_choices())
async def set_alert_channel_slash(
    interaction: discord.Interaction,
    server: str = DEFAULT_BOSS_SERVER,
    channel: Optional[discord.TextChannel] = None,
) -> None:
    try:
        boss_server = normalize_boss_server(server)
    except ValueError:
        await interaction.response.send_message(f"server ต้องเป็น {', '.join(SUPPORTED_BOSS_SERVERS)}", ephemeral=True)
        return

    target = channel or interaction.channel
    if target is None:
        await interaction.response.send_message("ไม่พบ channel สำหรับตั้งค่า", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        await set_alert_channel_value(target, boss_server)
    except discord.HTTPException as error:
        await interaction.followup.send(f"บอทส่งข้อความใน channel นี้ไม่ได้: `{error}`", ephemeral=True)
        return

    await interaction.followup.send(f"ตั้งค่าห้องแจ้งเตือนบอส Server {boss_server} เป็น {target.mention} แล้ว", ephemeral=True)


@bot.tree.command(name="boss_status", description="ดูเวลาเกิดรอบถัดไปของบอส")
@discord.app_commands.choices(server=boss_server_choices())
async def boss_status_slash(
    interaction: discord.Interaction,
    server: str = DEFAULT_BOSS_SERVER,
    page: int = 1,
) -> None:
    reload_boss_data()
    try:
        boss_server = normalize_boss_server(server)
    except ValueError:
        await interaction.response.send_message(f"server ต้องเป็น {', '.join(SUPPORTED_BOSS_SERVERS)}", ephemeral=True)
        return

    await send_boss_status_page_interaction(interaction, page - 1, server=boss_server, ephemeral=True)


@bot.command(name="find_boss_spawn")
async def find_boss_spawn(ctx: commands.Context) -> None:
    reload_boss_data()
    await send_private_command_result(ctx, build_find_boss_spawn_message())


@bot.tree.command(name="find_boss_spawn", description="ดูบอสที่ใกล้เวลาเกิดที่สุด 5 ตัว")
async def find_boss_spawn_slash(interaction: discord.Interaction) -> None:
    reload_boss_data()
    await interaction.response.send_message(build_find_boss_spawn_message(), ephemeral=True)


@bot.command(name="boss_killed")
async def boss_killed(ctx: commands.Context) -> None:
    reload_boss_data()
    await send_private_command_result(ctx, embed=build_setup_embed(), view=BossView())


@bot.tree.command(name="boss_killed", description="เปิดเมนูเลือกบอสที่ตายแล้ว")
async def boss_killed_slash(interaction: discord.Interaction) -> None:
    reload_boss_data()
    await interaction.response.send_message(embed=build_setup_embed(), view=BossView(), ephemeral=True)


@bot.command(name="boss_killed_id")
async def boss_killed_id(ctx: commands.Context, boss_id: str) -> None:
    try:
        boss, next_spawn = record_boss_kill(boss_id)
    except KeyError:
        await send_private_command_error(ctx, f"ไม่พบ `{boss_id}` ใช้ `!boss_status` เพื่อดูรายการบอส")
        return

    await send_private_command_result(ctx, embed=build_kill_record_embed(boss, next_spawn, ctx.author))


@bot.tree.command(name="boss_killed_id", description="บันทึกว่าบอสตายด้วย boss id")
async def boss_killed_id_slash(interaction: discord.Interaction, boss_id: str) -> None:
    try:
        boss, next_spawn = record_boss_kill(boss_id)
    except KeyError:
        await interaction.response.send_message(
            f"ไม่พบ `{boss_id}` ใช้ `/boss_status` เพื่อดูรายการบอส",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        embed=build_kill_record_embed(boss, next_spawn, interaction.user),
        ephemeral=True,
    )


@bot.command(name="add_boss")
@commands.has_permissions(manage_guild=True)
async def add_boss(ctx: commands.Context, boss_id: str, cooldown_minutes: int, *, details: str) -> None:
    reload_boss_data()
    try:
        boss_server, boss_channel, element, race, name = parse_add_boss_details(details)
    except ValueError:
        await send_private_command_error(ctx, f"server ต้องเป็น {', '.join(SUPPORTED_BOSS_SERVERS)}")
        return

    if not name:
        await send_private_command_error(
            ctx,
            "กรุณาใส่ข้อมูลบอส เช่น `!add_boss boss_lv100 480 Austeja | CH-1 | ไฟ | มังกร | บอสเวล 100 (Phoenix)`"
        )
        return

    stored_boss_id = boss_id_for_channel(boss_id, boss_channel, boss_server)
    if stored_boss_id in boss_data:
        await send_private_command_error(
            ctx,
            f"`{stored_boss_id}` มีอยู่แล้ว สำหรับ `{boss_id_base(boss_id)}` server `{boss_server}` channel `{boss_channel}`"
        )
        return

    boss_data[stored_boss_id] = {
        "name": name,
        "server": boss_server,
        "channel": boss_channel,
        "element": element,
        "race": race,
        "cooldown_minutes": cooldown_minutes,
        "next_spawn": None,
        "alert_sent": False,
    }
    save_boss_data(boss_data)
    message = build_add_boss_message(stored_boss_id, boss_data[stored_boss_id], "!setup_boss")
    try:
        await ctx.author.send(message)
    except discord.HTTPException:
        await send_private_command_error(ctx, "เพิ่มบอสสำเร็จแล้ว แต่บอทส่ง DM ให้ไม่ได้ กรุณาเปิดรับข้อความส่วนตัวจากสมาชิกในเซิร์ฟเวอร์นี้")
        return

    try:
        await ctx.message.add_reaction("✅")
    except discord.HTTPException:
        pass


@bot.tree.command(name="add_boss", description="เพิ่มบอสใหม่")
@discord.app_commands.default_permissions(manage_guild=True)
async def add_boss_slash(
    interaction: discord.Interaction,
    boss_id: str,
    cooldown_minutes: int,
    name: str,
    server: str = DEFAULT_BOSS_SERVER,
    channel: str = "ไม่ระบุ",
    element: str = "ไม่ระบุ",
    race: str = "ไม่ระบุ",
) -> None:
    reload_boss_data()
    try:
        boss_server = normalize_boss_server(server)
    except ValueError:
        await interaction.response.send_message(f"server ต้องเป็น {', '.join(SUPPORTED_BOSS_SERVERS)}", ephemeral=True)
        return

    stored_boss_id = boss_id_for_channel(boss_id, channel, boss_server)
    if stored_boss_id in boss_data:
        await interaction.response.send_message(
            f"`{stored_boss_id}` มีอยู่แล้ว สำหรับ `{boss_id_base(boss_id)}` server `{boss_server}` channel `{channel}`",
            ephemeral=True,
        )
        return

    boss_data[stored_boss_id] = {
        "name": name,
        "server": boss_server,
        "channel": channel,
        "element": element,
        "race": race,
        "cooldown_minutes": cooldown_minutes,
        "next_spawn": None,
        "alert_sent": False,
    }
    save_boss_data(boss_data)
    await interaction.response.send_message(
        build_add_boss_message(stored_boss_id, boss_data[stored_boss_id], "/setup_boss"),
        ephemeral=True,
    )


@bot.command(name="edit_boss")
@commands.has_permissions(manage_guild=True)
async def edit_boss(ctx: commands.Context, boss_id: str, *, details: str) -> None:
    try:
        updates = parse_edit_boss_details(details)
    except (ValueError, TypeError):
        await send_private_command_error(
            ctx,
            "รูปแบบไม่ถูกต้อง ใช้ `!edit_boss boss_id ชื่อ | server | channel | ธาตุ | เผ่า | cooldown_minutes` "
            "และใส่ `-` เพื่อคงค่าเดิม"
        )
        return

    if not updates:
        await send_private_command_error(ctx, "ไม่มีข้อมูลที่ต้องแก้ไข")
        return

    try:
        old_boss_id, new_boss_id, boss = update_boss_details(boss_id, updates)
    except KeyError:
        await send_private_command_error(ctx, f"ไม่พบ `{boss_id}`")
        return
    except ValueError as error:
        await send_private_command_error(ctx, str(error))
        return

    try:
        await ctx.author.send(build_edit_boss_message(old_boss_id, new_boss_id, boss))
    except discord.HTTPException:
        await send_private_command_error(ctx, "แก้ไขข้อมูลบอสสำเร็จแล้ว แต่บอทส่ง DM ให้ไม่ได้")
        return

    try:
        await ctx.message.add_reaction("✅")
    except discord.HTTPException:
        pass


@bot.tree.command(name="edit_boss", description="แก้ไขรายละเอียดบอส")
@discord.app_commands.default_permissions(manage_guild=True)
async def edit_boss_slash(
    interaction: discord.Interaction,
    boss_id: str,
    name: Optional[str] = None,
    server: Optional[str] = None,
    channel: Optional[str] = None,
    element: Optional[str] = None,
    race: Optional[str] = None,
    cooldown_minutes: Optional[int] = None,
) -> None:
    updates: dict[str, Any] = {
        "name": name,
        "server": server,
        "channel": channel,
        "element": element,
        "race": race,
        "cooldown_minutes": cooldown_minutes,
    }
    updates = {key: value for key, value in updates.items() if value is not None}
    if not updates:
        await interaction.response.send_message("กรุณาระบุข้อมูลอย่างน้อย 1 ช่องที่ต้องการแก้ไข", ephemeral=True)
        return

    try:
        old_boss_id, new_boss_id, boss = update_boss_details(boss_id, updates)
    except KeyError:
        await interaction.response.send_message(f"ไม่พบ `{boss_id}`", ephemeral=True)
        return
    except ValueError as error:
        await interaction.response.send_message(str(error), ephemeral=True)
        return

    await interaction.response.send_message(
        build_edit_boss_message(old_boss_id, new_boss_id, boss),
        ephemeral=True,
    )


@bot.command(name="delete_boss")
@commands.has_permissions(manage_guild=True)
async def delete_boss(ctx: commands.Context, boss_id: str) -> None:
    try:
        stored_boss_id, boss = delete_boss_data(boss_id)
    except KeyError:
        await send_private_command_error(ctx, f"ไม่พบ `{boss_id}`")
        return

    try:
        await ctx.author.send(build_delete_boss_message(stored_boss_id, boss))
    except discord.HTTPException:
        await send_private_command_error(ctx, "ลบบอสสำเร็จแล้ว แต่บอทส่ง DM ให้ไม่ได้")
        return

    try:
        await ctx.message.add_reaction("✅")
    except discord.HTTPException:
        pass


@bot.tree.command(name="delete_boss", description="ลบข้อมูลบอส")
@discord.app_commands.default_permissions(manage_guild=True)
async def delete_boss_slash(interaction: discord.Interaction, boss_id: str) -> None:
    try:
        stored_boss_id, boss = delete_boss_data(boss_id)
    except KeyError:
        await interaction.response.send_message(f"ไม่พบ `{boss_id}`", ephemeral=True)
        return

    await interaction.response.send_message(
        build_delete_boss_message(stored_boss_id, boss),
        ephemeral=True,
    )


@bot.command(name="clear_boss")
@commands.has_permissions(manage_guild=True)
async def clear_boss(ctx: commands.Context, boss_id: str) -> None:
    reload_boss_data()
    try:
        stored_boss_id = resolve_boss_id(boss_id)
    except KeyError:
        await send_private_command_error(ctx, f"ไม่พบ `{boss_id}`")
        return

    boss_data[stored_boss_id]["next_spawn"] = None
    boss_data[stored_boss_id]["alert_sent"] = False
    boss_data[stored_boss_id].pop("alert_message_id", None)
    save_boss_record(stored_boss_id, boss_data[stored_boss_id])
    await send_private_command_result(ctx, f"ล้างเวลาเกิดของ **{boss_data[stored_boss_id]['name']}** แล้ว")


@bot.tree.command(name="clear_boss", description="ล้างเวลาที่บันทึกไว้ของบอส")
@discord.app_commands.default_permissions(manage_guild=True)
async def clear_boss_slash(interaction: discord.Interaction, boss_id: str) -> None:
    reload_boss_data()
    try:
        stored_boss_id = resolve_boss_id(boss_id)
    except KeyError:
        await interaction.response.send_message(f"ไม่พบ `{boss_id}`", ephemeral=True)
        return

    boss_data[stored_boss_id]["next_spawn"] = None
    boss_data[stored_boss_id]["alert_sent"] = False
    boss_data[stored_boss_id].pop("alert_message_id", None)
    save_boss_record(stored_boss_id, boss_data[stored_boss_id])
    await interaction.response.send_message(f"ล้างเวลาเกิดของ **{boss_data[stored_boss_id]['name']}** แล้ว", ephemeral=True)


@bot.command(name="set_cooldown_boss")
@commands.has_permissions(manage_guild=True)
async def set_cooldown_boss(ctx: commands.Context, server: Optional[str] = None, page: int = 1) -> None:
    reload_boss_data()
    try:
        if server and server.isdigit():
            page = int(server)
            boss_server = infer_boss_server_from_channel(ctx.channel)
        else:
            boss_server = normalize_boss_server(server) if server else infer_boss_server_from_channel(ctx.channel)
    except ValueError:
        await send_private_command_error(ctx, f"server ต้องเป็น {', '.join(SUPPORTED_BOSS_SERVERS)}")
        return

    page = max(1, page)
    sent_count, total_count = await send_missing_cooldown_alerts(ctx.channel, boss_server, page=page)
    if sent_count == 0:
        await ctx.reply(f"ไม่มีบอสของ Server {boss_server} ที่ `cooldown_minutes` เป็น `0` ในหน้านี้", delete_after=12)
        return

    total_pages = max(1, (total_count + MISSING_COOLDOWN_ALERTS_PER_PAGE - 1) // MISSING_COOLDOWN_ALERTS_PER_PAGE)
    if page < total_pages:
        await ctx.reply(
            f"ส่งรายการ Server {boss_server} หน้า {page}/{total_pages} แล้ว "
            f"ถ้าต้องการหน้าถัดไปใช้ `!set_cooldown_boss {boss_server} {page + 1}`",
            delete_after=20,
        )

    try:
        await ctx.message.add_reaction("✅")
    except discord.HTTPException:
        pass


@bot.tree.command(name="set_cooldown_boss", description="ส่งรายการบอสที่ยังไม่มีคูลดาวน์ไปยังห้องนี้")
@discord.app_commands.default_permissions(manage_guild=True)
@discord.app_commands.choices(server=boss_server_choices())
async def set_cooldown_boss_slash(
    interaction: discord.Interaction,
    server: Optional[str] = None,
    page: int = 1,
) -> None:
    reload_boss_data()
    try:
        boss_server = normalize_boss_server(server) if server else infer_boss_server_from_channel(interaction.channel)
    except ValueError:
        await interaction.response.send_message(f"server ต้องเป็น {', '.join(SUPPORTED_BOSS_SERVERS)}", ephemeral=True)
        return

    if interaction.channel is None:
        await interaction.response.send_message("ไม่พบห้องสำหรับส่งข้อความ", ephemeral=True)
        return

    page = max(1, page)
    await interaction.response.defer(ephemeral=True)
    sent_count, total_count = await send_missing_cooldown_alerts(interaction.channel, boss_server, page=page)
    if sent_count == 0:
        await interaction.followup.send(
            f"ไม่มีบอสของ Server {boss_server} ที่ `cooldown_minutes` เป็น `0` ในหน้านี้",
            ephemeral=True,
        )
        return

    total_pages = max(1, (total_count + MISSING_COOLDOWN_ALERTS_PER_PAGE - 1) // MISSING_COOLDOWN_ALERTS_PER_PAGE)
    await interaction.followup.send(
        f"ส่งข้อความตั้งค่าคูลดาวน์ของ Server {boss_server} หน้า {page}/{total_pages} "
        f"ไปยังห้องนี้แล้ว {sent_count} รายการ",
        ephemeral=True,
    )


@bot.command(name="reset_cooldown_boss")
@commands.has_permissions(manage_guild=True)
async def reset_cooldown_boss(ctx: commands.Context, boss_id: Optional[str] = None) -> None:
    try:
        updated_bosses = reset_boss_cooldowns(boss_id)
    except KeyError:
        await send_private_command_error(ctx, f"ไม่พบ `{boss_id}`")
        return

    if boss_id is None:
        await send_private_command_result(ctx, f"ตั้งค่า `cooldown_minutes` เป็น `0` ให้บอสทั้งหมด {len(updated_bosses)} ตัวแล้ว")
        return

    await send_private_command_result(ctx, f"ตั้งค่า `cooldown_minutes` ของ **{updated_bosses[0][1]['name']}** เป็น `0` แล้ว")


@bot.tree.command(name="reset_cooldown_boss", description="ตั้ง cooldown_minutes เป็น 0 ให้บอสทั้งหมดหรือบอสที่ระบุ")
@discord.app_commands.default_permissions(manage_guild=True)
async def reset_cooldown_boss_slash(
    interaction: discord.Interaction,
    boss_id: Optional[str] = None,
) -> None:
    try:
        updated_bosses = reset_boss_cooldowns(boss_id)
    except KeyError:
        await interaction.response.send_message(f"ไม่พบ `{boss_id}`", ephemeral=True)
        return

    if boss_id is None:
        await interaction.response.send_message(
            f"ตั้งค่า `cooldown_minutes` เป็น `0` ให้บอสทั้งหมด {len(updated_bosses)} ตัวแล้ว",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"ตั้งค่า `cooldown_minutes` ของ **{updated_bosses[0][1]['name']}** เป็น `0` แล้ว",
        ephemeral=True,
    )


@bot.command(name="set_boss_cooldown")
@commands.has_permissions(manage_guild=True)
async def set_boss_cooldown(ctx: commands.Context, boss_id: str, countdown: str) -> None:
    try:
        cooldown_minutes = parse_countdown_minutes(countdown)
    except ValueError:
        await send_private_command_error(ctx, "รูปแบบเวลาไม่ถูกต้อง กรุณาใส่เป็น `HHMM` หรือ `HH:MM` เช่น `0030`, `01:35`")
        return

    try:
        boss, next_spawn = update_boss_cooldown(boss_id, cooldown_minutes)
    except KeyError:
        await send_private_command_error(ctx, f"ไม่พบ `{boss_id}`")
        return

    message = build_set_boss_cooldown_message(
        boss,
        format_countdown_minutes(cooldown_minutes),
        cooldown_minutes,
        next_spawn,
    )
    try:
        await ctx.author.send(message)
    except discord.HTTPException:
        await send_private_command_error(ctx, "ตั้งค่าคูลดาวน์สำเร็จแล้ว แต่บอทส่ง DM ให้ไม่ได้ กรุณาเปิดรับข้อความส่วนตัวจากสมาชิกในเซิร์ฟเวอร์นี้")
        return

    try:
        await ctx.message.add_reaction("✅")
    except discord.HTTPException:
        pass


@bot.tree.command(name="set_boss_cooldown", description="ตั้ง cooldown ของบอสด้วยรูปแบบ HHMM หรือ HH:MM")
@discord.app_commands.default_permissions(manage_guild=True)
async def set_boss_cooldown_slash(
    interaction: discord.Interaction,
    boss_id: str,
    countdown: str,
) -> None:
    try:
        cooldown_minutes = parse_countdown_minutes(countdown)
    except ValueError:
        await interaction.response.send_message(
            "รูปแบบเวลาไม่ถูกต้อง กรุณาใส่เป็น `HHMM` หรือ `HH:MM` เช่น `0030`, `01:35`",
            ephemeral=True,
        )
        return

    try:
        boss, next_spawn = update_boss_cooldown(boss_id, cooldown_minutes)
    except KeyError:
        await interaction.response.send_message(f"ไม่พบ `{boss_id}`", ephemeral=True)
        return

    await interaction.response.send_message(
        build_set_boss_cooldown_message(
            boss,
            format_countdown_minutes(cooldown_minutes),
            cooldown_minutes,
            next_spawn,
        ),
        ephemeral=True,
    )


@setup_boss.error
@add_boss.error
@edit_boss.error
@delete_boss.error
@clear_boss.error
@set_alert_channel.error
@reset_cooldown_boss.error
@set_cooldown_boss.error
@set_boss_cooldown.error
async def admin_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.MissingPermissions):
        await send_private_command_error(ctx, "คำสั่งนี้ใช้ได้เฉพาะคนที่มีสิทธิ์จัดการเซิร์ฟเวอร์")
    else:
        await send_private_command_error(ctx, f"ใช้คำสั่งไม่ถูกต้อง: `{error}`")


if not TOKEN:
    raise RuntimeError("Please set DISCORD_TOKEN in .env before running the bot.")

bot.run(TOKEN)

