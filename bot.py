"""ALW Academy Telegram funnel bot.

Install: ``pip install aiogram aiosqlite``
Run: set BOT_TOKEN and ADMIN_ID (and optionally PROMO_VIDEO_PATH), then
``python bot.py``.

PROMO_VIDEO_PATH accepts a local photo/video path or a Telegram file_id.  For
file IDs use ``photo:<file_id>`` when the file is a photo; a bare ID is treated
as a video for backwards compatibility with the setting name.  A bare photo
file_id can be selected with the optional ``PROMO_MEDIA_TYPE=photo`` setting.
"""

from __future__ import annotations

import asyncio
import csv
import html
import io
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ChatJoinRequest,
    ErrorEvent,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)


# ------------------------------- Configuration -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PROMO_VIDEO_PATH = os.getenv("PROMO_VIDEO_PATH", "")
# Optional when PROMO_VIDEO_PATH is a bare file_id ("photo" or "video").
PROMO_MEDIA_TYPE = os.getenv("PROMO_MEDIA_TYPE", "")
CHECKLIST_FILE_PATH = os.getenv("CHECKLIST_FILE_PATH", "/app/checklist.pdf")
BOT_USERNAME = os.getenv("BOT_USERNAME", "alwacademybot")
DB_PATH = os.getenv("DB_PATH", "/app/data/users.db")
SITE_URL = os.getenv("SITE_URL", "https://alwacademy.ru")
CHECKLIST_URL = os.getenv("CHECKLIST_URL", "https://alwacademy.ru/articles/test-1792970529333?key=3YbR8qAzsv7XOE1g5HDvOOWVDhxcdE_D")

FOLLOWUP_DELAYS_SECONDS = (15 * 60, 24 * 60 * 60, 24 * 60 * 60)
BROADCAST_DELAY_SECONDS = 0.05
ADMIN_EVENT_NOTIFICATIONS = os.getenv("ADMIN_EVENT_NOTIFICATIONS", "1").lower() not in {"0", "false", "no"}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("alw_academy_bot")

router = Router()
followup_tasks: dict[int, asyncio.Task[None]] = {}


WELCOME_TEXT_APPROVED = """Привет, {name} 👋

Заявку в канал <b>ALW | Лидогенерация</b> одобрил — добро пожаловать.

Дарю чек-лист <b>«100 000 ₽ на трафике»</b> — как мы зарабатываем, приводя клиентов в банки, беттинг и МФО. С реальными ставками по банкам и кейсом, где девушка за месяц сделала больше миллиона.

Сначала два коротких вопроса — чтобы понимать, с чего тебе начинать. Займёт 10 секунд. Жми /start 👇

Полный маршрут, статьи и бесплатная запись на созвон — на сайте ALW Academy."""

QUESTION_1_TEXT = "<b>Вопрос 1 из 2.</b>\n\nТы сейчас зарабатываешь в интернете?"
QUESTION_2_TEXT = "<b>Вопрос 2 из 2.</b>\n\nЗнаешь, что такое РКО?"

CHECKLIST_CAPTION = """Держи 👇

Главное, что стоит запомнить: 100 000 ₽ — это 2–3 человека в месяц, не сотня.

Банки и условия — на нас. А как стать учеником ALW Academy и пройти путь до первых выплат — рассказываю на сайте."""

FOLLOWUP_TEXT_1 = """⚡️ <b>Кстати, важный момент!</b>

На этой неделе наши кураторы проводят бесплатные 10-минутные разборы для новичков: показывают актуальные ставки по банкам (РКО и карты) и помогают составить личный план выхода на первые выплаты.

Мест на разбор немного. Забронировать бесплатный слот можно на сайте ALW Academy или написать напрямую @ALWAcademy."""

FOLLOWUP_TEXT_2 = """👀 <b>Чек-лист ещё не открыл?</b>

Там одна цифра, которая меняет взгляд на нишу: <b>43 925 ₽ с одного клиента только на банках.</b>

Открой материал на сайте, пока он не утонул в чатах 👇"""

FOLLOWUP_TEXT_3 = """🎓 <b>Хочешь научиться зарабатывать на банковских продуктах?</b>

В ALW Academy мы с нуля разбираем, как устроена работа с банками, где искать клиентов, как оформлять заявки и как выстраивать стабильную связку.

Даём понятные материалы, рабочие примеры и поддержку по шагам — чтобы ты мог начать без хаоса и лишней теории.

Подробнее об обучении — на сайте ALW Academy 👇"""

FOLLOWUP_INTEREST_TEXT = """Отлично 🙌 Заявку на обучение получил — с тобой свяжутся в ближайшее время.

Чтобы разговор был предметнее, напиши одним сообщением: сколько времени в день готов уделять и есть ли уже люди на примете.

Подробности и запись на сайте ALW Academy 👇"""


async def site_url() -> str:
    return (await db.get_setting("site_url") or SITE_URL).strip()


async def welcome_keyboard() -> InlineKeyboardMarkup:
    site = await site_url()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Начать /start", url=f"https://t.me/{BOT_USERNAME}?start=questionnaire")],
            [InlineKeyboardButton(text="🌐 Открыть сайт ALW Academy", url=site)],
        ]
    )


def question_1_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Да, уже зарабатываю", callback_data="q1_yes")],
            [InlineKeyboardButton(text="Пробую, но пока слабо", callback_data="q1_try")],
            [InlineKeyboardButton(text="Нет, присматриваюсь", callback_data="q1_no")],
        ]
    )


def question_2_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Да, знаю", callback_data="q2_yes")],
            [InlineKeyboardButton(text="Слышал, но не разбирался", callback_data="q2_heard")],
            [InlineKeyboardButton(text="Впервые слышу", callback_data="q2_no")],
        ]
    )


def checklist_keyboard(url: str = "") -> InlineKeyboardMarkup:
    rows = []
    if url:
        rows.append([InlineKeyboardButton(text="📖 Открыть материал", url=url)])
    rows.append([InlineKeyboardButton(text="Прочитал(а) ✅", callback_data="checklist_read")])
    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


async def followup_keyboard(step: int = 1) -> InlineKeyboardMarkup:
    site = await site_url()
    if step == 3:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎓 Стать учеником", callback_data="followup_interest")],
                [InlineKeyboardButton(text="🌐 Открыть сайт ALW Academy", url=site)],
            ]
        )
    if step == 2:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📖 Открыть материал", url=await content_value("checklist_url", CHECKLIST_URL))],
                [InlineKeyboardButton(text="📞 Записаться на разбор", url=f"{site}/calls")],
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Записаться на бесплатный разбор", url=f"{site}/calls")],
            [InlineKeyboardButton(text="🌐 Открыть сайт ALW Academy", url=site)],
        ]
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT NOT NULL DEFAULT '',
                joined_at TEXT,
                joined_via_request INTEGER NOT NULL DEFAULT 0,
                lead_clicked INTEGER NOT NULL DEFAULT 0,
                lead_clicked_at TEXT,
                blocked INTEGER NOT NULL DEFAULT 0
            )"""
        )
        await self.conn.execute(
            """CREATE TABLE IF NOT EXISTS followups (
                user_id INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                due_at REAL NOT NULL,
                sent INTEGER NOT NULL DEFAULT 0
            )"""
        )
        existing_columns = {
            row[1]
            for row in await (await self.conn.execute("PRAGMA table_info(users)")).fetchall()
        }
        migrations = {
            "q1_answer": "TEXT",
            "q2_answer": "TEXT",
            "checklist_sent_at": "TEXT",
            "checklist_read": "INTEGER NOT NULL DEFAULT 0",
            "checklist_read_at": "TEXT",
            "first_seen_at": "TEXT",
            "last_seen_at": "TEXT",
            "source": "TEXT",
            "source_label": "TEXT",
            "start_payload": "TEXT",
            "last_event": "TEXT",
        }
        for column, definition in migrations.items():
            if column not in existing_columns:
                await self.conn.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
        followup_columns = {
            row[1]
            for row in await (await self.conn.execute("PRAGMA table_info(followups)")).fetchall()
        }
        if "step" not in followup_columns:
            await self.conn.execute("ALTER TABLE followups ADD COLUMN step INTEGER NOT NULL DEFAULT 1")
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        await self.conn.execute(
            """CREATE TABLE IF NOT EXISTS button_clicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                button TEXT NOT NULL,
                clicked_at TEXT NOT NULL
            )"""
        )
        await self.conn.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                event TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                occurred_at TEXT NOT NULL
            )"""
        )
        await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON events(occurred_at)")
        await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, occurred_at)")
        await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_users_source ON users(source, source_label)")
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()

    async def upsert_user(
        self,
        user: Any,
        *,
        joined_at: str | None,
        joined_via_request: bool,
        source: str | None = None,
        source_label: str | None = None,
        start_payload: str | None = None,
        last_event: str | None = None,
    ) -> bool:
        assert self.conn is not None
        now = utc_now()
        async with self.conn.execute("SELECT 1 FROM users WHERE user_id=?", (user.id,)) as cursor:
            is_new = await cursor.fetchone() is None
        await self.conn.execute(
            """INSERT INTO users (
                   user_id, username, first_name, joined_at, joined_via_request,
                   first_seen_at, last_seen_at, source, source_label, start_payload, last_event
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 username=excluded.username,
                 first_name=excluded.first_name,
                 joined_at=COALESCE(excluded.joined_at, users.joined_at),
                 joined_via_request=MAX(users.joined_via_request, excluded.joined_via_request),
                 first_seen_at=COALESCE(users.first_seen_at, excluded.first_seen_at),
                 last_seen_at=excluded.last_seen_at,
                 source=COALESCE(excluded.source, users.source),
                 source_label=COALESCE(excluded.source_label, users.source_label),
                 start_payload=COALESCE(excluded.start_payload, users.start_payload),
                 last_event=COALESCE(excluded.last_event, users.last_event)""",
            (
                user.id,
                user.username,
                user.first_name or "",
                joined_at,
                int(joined_via_request),
                now,
                now,
                source,
                source_label,
                start_payload,
                last_event,
            ),
        )
        await self.conn.commit()
        return is_new

    async def touch_user(self, user_id: int, event: str) -> None:
        assert self.conn is not None
        await self.conn.execute(
            "UPDATE users SET last_seen_at=?, last_event=? WHERE user_id=?",
            (utc_now(), event, user_id),
        )
        await self.conn.commit()

    async def record_event(
        self,
        user_id: int | None,
        event: str,
        *,
        label: str = "",
        payload: dict[str, Any] | None = None,
        source: str = "",
    ) -> None:
        assert self.conn is not None
        encoded = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
        await self.conn.execute(
            "INSERT INTO events(user_id,event,label,payload,source,occurred_at) VALUES (?,?,?,?,?,?)",
            (user_id, event, label, encoded, source, utc_now()),
        )
        if user_id is not None:
            await self.conn.execute(
                "UPDATE users SET last_seen_at=?, last_event=? WHERE user_id=?",
                (utc_now(), event, user_id),
            )
        await self.conn.commit()

    async def recent_events(self, limit: int = 20) -> list[aiosqlite.Row]:
        assert self.conn is not None
        async with self.conn.execute(
            "SELECT e.*, u.username, u.first_name FROM events e LEFT JOIN users u ON u.user_id=e.user_id "
            "ORDER BY e.id DESC LIMIT ?",
            (limit,),
        ) as cursor:
            return await cursor.fetchall()

    async def event_statistics(self, since: str | None = None) -> list[tuple[str, int, int]]:
        assert self.conn is not None
        query = "SELECT event, COUNT(*) AS total, COUNT(DISTINCT user_id) AS users FROM events"
        params: tuple[Any, ...] = ()
        if since:
            query += " WHERE occurred_at >= ?"
            params = (since,)
        query += " GROUP BY event ORDER BY total DESC"
        async with self.conn.execute(query, params) as cursor:
            return [(str(row[0]), int(row[1]), int(row[2] or 0)) for row in await cursor.fetchall()]

    async def source_statistics(self, since: str | None = None) -> list[tuple[str, str, int, int]]:
        assert self.conn is not None
        query = (
            "SELECT COALESCE(NULLIF(label,''), NULLIF(source,''), 'Без метки') AS label, "
            "COALESCE(source,'') AS source, COUNT(*) AS total, COUNT(DISTINCT user_id) AS users "
            "FROM events WHERE event IN ('start','join_request')"
        )
        params: list[Any] = []
        if since:
            query += " AND occurred_at >= ?"
            params.append(since)
        query += " GROUP BY label, source ORDER BY total DESC"
        async with self.conn.execute(query, tuple(params)) as cursor:
            return [(str(row[0]), str(row[1]), int(row[2]), int(row[3] or 0)) for row in await cursor.fetchall()]

    async def summary(self, since: str | None = None) -> dict[str, int]:
        assert self.conn is not None
        where = "WHERE first_seen_at >= ?" if since else ""
        params: tuple[Any, ...] = (since,) if since else ()
        async with self.conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(joined_via_request),0), COALESCE(SUM(blocked),0), "
            f"COALESCE(SUM(checklist_read),0), COALESCE(SUM(q2_answer IS NOT NULL),0), "
            f"COALESCE(SUM(checklist_sent_at IS NOT NULL),0) FROM users {where}", params,
        ) as cursor:
            row = await cursor.fetchone()
        event_where = "WHERE occurred_at >= ?" if since else ""
        async with self.conn.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT user_id) FROM events {event_where}", params,
        ) as cursor:
            event_row = await cursor.fetchone()
        return {
            "users": int(row[0] or 0),
            "joined": int(row[1] or 0),
            "blocked": int(row[2] or 0),
            "read": int(row[3] or 0),
            "answered": int(row[4] or 0),
            "sent": int(row[5] or 0),
            "events": int(event_row[0] or 0),
            "active": int(event_row[1] or 0),
        }

    async def detailed_user(self, user_id: int) -> tuple[aiosqlite.Row | None, list[aiosqlite.Row]]:
        assert self.conn is not None
        async with self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cursor:
            user = await cursor.fetchone()
        async with self.conn.execute(
            "SELECT * FROM events WHERE user_id=? ORDER BY id DESC LIMIT 100", (user_id,)
        ) as cursor:
            events = await cursor.fetchall()
        return user, events

    async def mark_lead_clicked(self, user_id: int) -> None:
        assert self.conn is not None
        await self.conn.execute(
            "UPDATE users SET lead_clicked=1, lead_clicked_at=? WHERE user_id=?",
            (utc_now(), user_id),
        )
        await self.conn.commit()

    async def save_questionnaire_answer(self, user_id: int, question: int, answer: str) -> None:
        assert self.conn is not None
        column = "q1_answer" if question == 1 else "q2_answer"
        await self.conn.execute(f"UPDATE users SET {column}=? WHERE user_id=?", (answer, user_id))
        await self.conn.commit()

    async def mark_checklist_sent(self, user_id: int) -> None:
        assert self.conn is not None
        now = utc_now()
        await self.conn.execute(
            "UPDATE users SET checklist_sent_at=?, lead_clicked=1, lead_clicked_at=? WHERE user_id=?",
            (now, now, user_id),
        )
        await self.conn.commit()

    async def mark_checklist_read(self, user_id: int) -> None:
        assert self.conn is not None
        await self.conn.execute(
            "UPDATE users SET checklist_read=1, checklist_read_at=? WHERE user_id=?",
            (utc_now(), user_id),
        )
        await self.conn.commit()

    async def questionnaire(self, user_id: int) -> dict[str, Any]:
        assert self.conn is not None
        async with self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else {}

    async def get_setting(self, key: str) -> str | None:
        assert self.conn is not None
        async with self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        assert self.conn is not None
        await self.conn.execute(
            "INSERT INTO settings(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value)
        )
        await self.conn.commit()

    async def delete_setting(self, key: str) -> None:
        assert self.conn is not None
        await self.conn.execute("DELETE FROM settings WHERE key=?", (key,))
        await self.conn.commit()

    async def waiting_for_checklist(self) -> list[int]:
        assert self.conn is not None
        async with self.conn.execute(
            "SELECT user_id FROM users WHERE q2_answer IS NOT NULL AND checklist_sent_at IS NULL"
        ) as cursor:
            return [int(row[0]) for row in await cursor.fetchall()]

    async def questionnaire_stats(self) -> tuple[int, int, int]:
        assert self.conn is not None
        async with self.conn.execute(
            "SELECT COUNT(q2_answer), COUNT(checklist_sent_at), COALESCE(SUM(checklist_read),0) FROM users"
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]), int(row[1]), int(row[2])

    async def record_button_click(self, user_id: int, button: str) -> None:
        assert self.conn is not None
        await self.conn.execute(
            "INSERT INTO button_clicks(user_id, button, clicked_at) VALUES (?, ?, ?)",
            (user_id, button, utc_now()),
        )
        await self.conn.execute(
            "INSERT INTO events(user_id,event,label,payload,source,occurred_at) VALUES (?,?,?,?,?,?)",
            (user_id, "button_click", button, "{}", "", utc_now()),
        )
        await self.conn.execute(
            "UPDATE users SET last_seen_at=?, last_event=? WHERE user_id=?",
            (utc_now(), "button_click:" + button, user_id),
        )
        await self.conn.commit()

    async def button_statistics(self) -> list[tuple[str, int, int]]:
        assert self.conn is not None
        async with self.conn.execute(
            """SELECT button, COUNT(*) AS total, COUNT(DISTINCT user_id) AS unique_users
               FROM button_clicks GROUP BY button ORDER BY total DESC"""
        ) as cursor:
            return [(str(row[0]), int(row[1]), int(row[2])) for row in await cursor.fetchall()]

    async def list_users(self, limit: int = 20, offset: int = 0) -> list[aiosqlite.Row]:
        assert self.conn is not None
        async with self.conn.execute(
            """SELECT user_id, username, first_name, joined_at, joined_via_request,
                      q1_answer, q2_answer, checklist_sent_at, checklist_read, blocked,
                      first_seen_at, last_seen_at, source, source_label, last_event
               FROM users ORDER BY COALESCE(joined_at, '') DESC, user_id DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ) as cursor:
            return await cursor.fetchall()

    async def user_detail(self, user_id: int) -> tuple[aiosqlite.Row | None, list[tuple[str, int, str | None]]]:
        assert self.conn is not None
        async with self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cursor:
            user = await cursor.fetchone()
        async with self.conn.execute(
            """SELECT button, COUNT(*) AS total, MAX(clicked_at) AS last_click
               FROM button_clicks WHERE user_id=? GROUP BY button ORDER BY total DESC""",
            (user_id,),
        ) as cursor:
            events = [(str(row[0]), int(row[1]), row[2]) for row in await cursor.fetchall()]
        return user, events

    async def export_users_csv(self) -> str:
        assert self.conn is not None
        async with self.conn.execute(
            """SELECT u.user_id, u.username, u.first_name, u.joined_at,
                      u.first_seen_at, u.last_seen_at, u.source, u.source_label, u.start_payload,
                      u.last_event, u.joined_via_request, u.q1_answer, u.q2_answer,
                      u.checklist_sent_at, u.checklist_read, u.blocked,
                      COALESCE((SELECT COUNT(*) FROM button_clicks b WHERE b.user_id=u.user_id),0)
               FROM users u ORDER BY COALESCE(u.joined_at,'') DESC"""
        ) as cursor:
            rows = await cursor.fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "user_id", "username", "first_name", "joined_at", "first_seen_at", "last_seen_at",
            "source", "source_label", "start_payload", "last_event", "joined_via_request",
            "q1_answer", "q2_answer", "checklist_sent_at", "checklist_read", "blocked", "button_clicks",
        ])
        writer.writerows(rows)
        return output.getvalue()

    async def export_events_csv(self) -> str:
        assert self.conn is not None
        async with self.conn.execute(
            """SELECT e.occurred_at, e.user_id, u.username, u.first_name,
                      e.event, e.label, e.source, e.payload
               FROM events e LEFT JOIN users u ON u.user_id=e.user_id
               ORDER BY e.id DESC"""
        ) as cursor:
            rows = await cursor.fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["occurred_at", "user_id", "username", "first_name", "event", "label", "source", "payload"])
        writer.writerows(rows)
        return output.getvalue()

    async def mark_blocked(self, user_id: int) -> None:
        assert self.conn is not None
        await self.conn.execute("UPDATE users SET blocked=1 WHERE user_id=?", (user_id,))
        await self.conn.commit()

    async def stats(self) -> tuple[int, int]:
        assert self.conn is not None
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(lead_clicked), 0) AS leads "
            "FROM users WHERE joined_via_request=1"
        )
        row = await cursor.fetchone()
        return int(row["total"]), int(row["leads"])

    async def all_user_ids(self) -> list[int]:
        assert self.conn is not None
        cursor = await self.conn.execute("SELECT user_id FROM users ORDER BY user_id")
        return [int(row[0]) for row in await cursor.fetchall()]

    async def save_followup(self, user_id: int, due_at: float, step: int = 1) -> float | None:
        assert self.conn is not None
        await self.conn.execute(
            """INSERT INTO followups(user_id, due_at, step, sent) VALUES (?, ?, ?, 0)
               ON CONFLICT(user_id) DO NOTHING""",
            (user_id, due_at, step),
        )
        await self.conn.commit()
        async with self.conn.execute("SELECT due_at,sent FROM followups WHERE user_id=?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        return float(row[0]) if row and not row[1] else None

    async def advance_followup(self, user_id: int, due_at: float, step: int) -> None:
        assert self.conn is not None
        await self.conn.execute(
            "UPDATE followups SET due_at=?, step=?, sent=0 WHERE user_id=?",
            (due_at, step, user_id),
        )
        await self.conn.commit()

    async def mark_followup_sent(self, user_id: int) -> None:
        assert self.conn is not None
        await self.conn.execute("UPDATE followups SET sent=1 WHERE user_id=?", (user_id,))
        await self.conn.commit()

    async def pending_followups(self) -> list[tuple[int, float, int]]:
        assert self.conn is not None
        cursor = await self.conn.execute("SELECT user_id, due_at, step FROM followups WHERE sent=0")
        return [(int(row[0]), float(row[1]), int(row[2])) for row in await cursor.fetchall()]


db = Database(DB_PATH)


def resolve_promo_media() -> tuple[str, str | FSInputFile] | None:
    """Return (photo|video, payload), accepting a path or a prefixed file ID."""
    value = PROMO_VIDEO_PATH.strip()
    if not value:
        return None
    lower = value.lower()
    for prefix, kind in (("photo:", "photo"), ("video:", "video")):
        if lower.startswith(prefix):
            return kind, value[len(prefix) :]
    path = Path(value)
    if path.is_file():
        image_suffixes = {".jpg", ".jpeg", ".png", ".webp"}
        return ("photo" if path.suffix.lower() in image_suffixes else "video"), FSInputFile(path)
    if PROMO_MEDIA_TYPE.lower() in {"photo", "video"}:
        return PROMO_MEDIA_TYPE.lower(), value
    # PROMO_VIDEO_PATH is named for the common case, so an unprefixed file_id is video.
    return "video", value


def display_name(user: Any) -> str:
    """Prefer a Telegram username, then fall back to the user's first name."""
    raw_name = f"@{user.username}" if getattr(user, "username", None) else (user.first_name or "друг")
    return html.escape(raw_name)


def start_payload(text: str | None) -> str:
    """Extract Telegram deep-link payload from a /start message."""
    raw = (text or "").strip()
    if not raw:
        return ""
    parts = raw.split(maxsplit=1)
    first = parts[0]
    if first.lower().startswith("/start="):
        return first.split("=", 1)[1].strip()
    if first.lower() == "/start" and len(parts) > 1:
        return parts[1].split(maxsplit=1)[0].strip()
    if first.lower().startswith("/start"):
        return first[6:].lstrip("_")
    return ""


def source_label_from_payload(payload: str) -> str:
    if not payload:
        return "Прямой вход"
    # Keep the label human-readable while preserving the original payload for
    # exact attribution. Campaign links can use /start=src_name or /start=name.
    return payload.removeprefix("src_").replace("_", " ").strip() or payload


async def notify_admin(bot: Bot, text: str) -> None:
    if not ADMIN_ID or not ADMIN_EVENT_NOTIFICATIONS:
        return
    try:
        await bot.send_message(ADMIN_ID, text)
    except TelegramAPIError:
        logger.exception("Could not send admin event notification")


async def resolve_checklist_file() -> str | FSInputFile | None:
    value = (await db.get_setting("checklist_file_id") or CHECKLIST_FILE_PATH).strip()
    if not value:
        return None
    path = Path(value)
    return FSInputFile(path) if path.is_file() else value


async def content_value(key: str, fallback: str) -> str:
    value = await db.get_setting(key)
    return value if value is not None else fallback


async def followup_text(step: int, user_id: int) -> str | None:
    if step == 3:
        user = await db.questionnaire(user_id)
        # Ответ «Да, знаю» означает, что пользователь уже знаком с РКО;
        # третий новичковый прогрев ему не отправляем.
        if user.get("q2_answer") == "Да, знаю":
            return None
    defaults = {1: FOLLOWUP_TEXT_1, 2: FOLLOWUP_TEXT_2, 3: FOLLOWUP_TEXT_3}
    value = await db.get_setting(f"followup_text_{step}")
    if value is None and step == 1:
        value = await db.get_setting("followup_text")
    return value if value is not None else defaults[step]


async def send_welcome(bot: Bot, chat_id: int, user: Any) -> Message:
    template = await content_value("welcome_text", WELCOME_TEXT_APPROVED)
    text = template.format(name=display_name(user))
    keyboard = await welcome_keyboard()
    media = resolve_promo_media()
    try:
        if media is None:
            return await bot.send_message(chat_id, text, reply_markup=keyboard)
        kind, payload = media
        if kind == "photo":
            return await bot.send_photo(chat_id, payload, caption=text, reply_markup=keyboard)
        return await bot.send_video(chat_id, payload, caption=text, reply_markup=keyboard)
    except (TelegramBadRequest, TelegramAPIError) as exc:
        # A stale file_id or a missing local file must not prevent the text funnel.
        logger.warning("Promo media failed for %s (%s); sending text fallback", chat_id, exc)
        return await bot.send_message(chat_id, text, reply_markup=keyboard)


async def send_checklist(bot: Bot, user_id: int) -> None:
    url = await content_value("checklist_url", CHECKLIST_URL)
    caption = await content_value("checklist_caption", CHECKLIST_CAPTION)
    await bot.send_message(user_id, caption, reply_markup=checklist_keyboard(url))
    await db.mark_checklist_sent(user_id)
    await db.record_event(user_id, "checklist_sent", label="Лид-магнит отправлен", payload={"url": url})
    await notify_admin(bot, f"📨 <b>Лид-магнит отправлен</b> · <code>{user_id}</code>")
    await schedule_followup(bot, user_id)


async def followup_worker(bot: Bot, user_id: int, due_at: float, step: int = 1) -> None:
    try:
        await asyncio.sleep(max(0.0, due_at - datetime.now(timezone.utc).timestamp()))
        text = await followup_text(step, user_id)
        if text is None:
            await db.mark_followup_sent(user_id)
            logger.info("Follow-up step %s skipped for experienced user %s", step, user_id)
            return
        await bot.send_message(user_id, text, reply_markup=await followup_keyboard(step))
        await db.mark_followup_sent(user_id)
        await db.record_event(user_id, "followup_sent", label=f"Дожим {step}")
        await notify_admin(bot, f"📨 <b>Дожим {step} отправлен</b> · <code>{user_id}</code>")
        logger.info("Follow-up step %s sent to %s", step, user_id)
        if step < len(FOLLOWUP_DELAYS_SECONDS):
            next_step = step + 1
            next_due_at = datetime.now(timezone.utc).timestamp() + FOLLOWUP_DELAYS_SECONDS[step]
            await db.advance_followup(user_id, next_due_at, next_step)
            followup_tasks[user_id] = asyncio.create_task(
                followup_worker(bot, user_id, next_due_at, next_step)
            )
    except TelegramForbiddenError:
        await db.mark_blocked(user_id)
        await db.mark_followup_sent(user_id)
        logger.info("User %s blocked the bot", user_id)
    except asyncio.CancelledError:
        raise
    except TelegramAPIError:
        logger.exception("Could not send follow-up to %s", user_id)
    finally:
        # A second click can replace this task while cancellation is being
        # delivered; never remove the replacement task from the registry.
        if followup_tasks.get(user_id) is asyncio.current_task():
            followup_tasks.pop(user_id, None)


async def schedule_followup(bot: Bot, user_id: int) -> None:
    due_at = datetime.now(timezone.utc).timestamp() + FOLLOWUP_DELAYS_SECONDS[0]
    saved_due_at = await db.save_followup(user_id, due_at, 1)
    if saved_due_at is None:
        return
    old_task = followup_tasks.get(user_id)
    if old_task and not old_task.done():
        return
    followup_tasks[user_id] = asyncio.create_task(followup_worker(bot, user_id, saved_due_at, 1))


class BroadcastState(StatesGroup):
    waiting_for_message = State()


class QuestionnaireState(StatesGroup):
    question_1 = State()
    question_2 = State()


class ChecklistState(StatesGroup):
    waiting_for_file = State()


class ContentState(StatesGroup):
    waiting_for_value = State()


def is_admin(message: Message) -> bool:
    return bool(ADMIN_ID) and message.from_user is not None and message.from_user.id == ADMIN_ID


def is_admin_user(user: Any) -> bool:
    return bool(ADMIN_ID) and user is not None and getattr(user, "id", None) == ADMIN_ID


CONTENT_FIELDS = {
    "welcome": ("welcome_text", "приветственное сообщение после одобрения заявки", WELCOME_TEXT_APPROVED),
    "checklist": ("checklist_caption", "текст перед ссылкой на материал", CHECKLIST_CAPTION),
    "checklist_url": ("checklist_url", "ссылка на материал-лидмагнит", CHECKLIST_URL),
    "followup": ("followup_text_1", "первое сообщение дожима через 15 минут", FOLLOWUP_TEXT_1),
    "followup_1": ("followup_text_1", "первое сообщение дожима через 15 минут", FOLLOWUP_TEXT_1),
    "followup_2": ("followup_text_2", "второе сообщение дожима на следующий день", FOLLOWUP_TEXT_2),
    "followup_3": ("followup_text_3", "третье сообщение дожима на следующий день", FOLLOWUP_TEXT_3),
    "followup_interest": ("followup_interest_text", "ответ после кнопки Стать учеником", FOLLOWUP_INTEREST_TEXT),
    "question_1": ("question_1_text", "первый вопрос анкеты", QUESTION_1_TEXT),
    "question_2": ("question_2_text", "второй вопрос анкеты", QUESTION_2_TEXT),
    "site": ("site_url", "ссылка на сайт", SITE_URL),
}


async def broadcast_one(bot: Bot, source: Message, user_id: int) -> bool:
    for attempt in range(4):
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=source.chat.id,
                message_id=source.message_id,
                reply_markup=source.reply_markup,
            )
            return True
        except TelegramRetryAfter as exc:
            logger.warning("FloodWait while sending to %s; sleeping %s seconds", user_id, exc.retry_after)
            await asyncio.sleep(exc.retry_after)
        except TelegramForbiddenError:
            await db.mark_blocked(user_id)
            return False
        except TelegramAPIError as exc:
            logger.warning("Broadcast to %s failed: %s", user_id, exc)
            return False
    return False


async def run_broadcast(bot: Bot, source: Message) -> tuple[int, int]:
    user_ids = await db.all_user_ids()
    delivered = 0
    failed = 0
    for user_id in user_ids:
        if await broadcast_one(bot, source, user_id):
            delivered += 1
        else:
            failed += 1
        await asyncio.sleep(BROADCAST_DELAY_SECONDS)
    return delivered, failed


@router.chat_join_request()
async def handle_join_request(request: ChatJoinRequest, bot: Bot) -> None:
    user = request.from_user
    invite = getattr(request, "invite_link", None)
    invite_url = str(getattr(invite, "invite_link", "") or "")
    invite_name = str(getattr(invite, "name", "") or "")
    source_label = invite_name or invite_url or "Заявка в канал"
    # Keep a record even if Telegram rejects a withdrawn/expired request; the
    # accepted count is updated only after approve_chat_join_request succeeds.
    is_new = await db.upsert_user(
        user,
        joined_at=None,
        joined_via_request=False,
        source="invite",
        source_label=source_label,
        last_event="join_request",
    )
    await db.record_event(
        user.id,
        "join_request",
        label=source_label,
        payload={"invite_url": invite_url, "invite_name": invite_name, "chat_id": request.chat.id},
        source="invite",
    )
    if is_new:
        await notify_admin(
            bot,
            f"🆕 <b>Новый пользователь</b>\n{display_name(user)} · <code>{user.id}</code>\n"
            f"Источник: <b>{html.escape(source_label)}</b>",
        )

    # Telegram grants the temporary user_chat_id permission until the request
    # is processed. Send the welcome first, then approve immediately; this is
    # the only reliable way to message someone who has never used /start.
    try:
        await send_welcome(bot, request.user_chat_id, user)
    except TelegramForbiddenError:
        await db.mark_blocked(user.id)
        logger.info("User %s blocked the bot or cannot receive the welcome", user.id)
    except TelegramAPIError:
        logger.exception("Could not send welcome to %s", user.id)

    try:
        await bot.approve_chat_join_request(chat_id=request.chat.id, user_id=user.id)
        await db.upsert_user(user, joined_at=utc_now(), joined_via_request=True)
        await db.record_event(user.id, "join_approved", label="Заявка в канал одобрена")
        logger.info("Approved join request: user=%s chat=%s", user.id, request.chat.id)
    except TelegramAPIError:
        logger.exception("Could not approve join request from %s", user.id)


async def start_questionnaire(bot: Bot, user: Any, state: FSMContext) -> None:
    await state.set_state(QuestionnaireState.question_1)
    await bot.send_message(user.id, await content_value("question_1_text", QUESTION_1_TEXT), reply_markup=question_1_keyboard())


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def handle_start(message: Message, bot: Bot, state: FSMContext) -> None:
    assert message.from_user is not None
    payload = start_payload(message.text)
    source = "deep_link" if payload else "direct"
    label = source_label_from_payload(payload)
    is_new = await db.upsert_user(
        message.from_user,
        joined_at=None,
        joined_via_request=False,
        source=source,
        source_label=label,
        start_payload=payload or None,
        last_event="start",
    )
    await db.record_event(
        message.from_user.id,
        "start",
        label=label,
        payload={"payload": payload, "text": message.text or ""},
        source=source,
    )
    if is_new:
        await notify_admin(
            bot,
            f"🆕 <b>Новый пользователь в боте</b>\n{display_name(message.from_user)} · "
            f"<code>{message.from_user.id}</code>\nИсточник: <b>{html.escape(label)}</b>",
        )
    try:
        await start_questionnaire(bot, message.from_user, state)
    except TelegramForbiddenError:
        await db.mark_blocked(message.from_user.id)
    except TelegramAPIError:
        logger.exception("Could not start questionnaire for %s", message.from_user.id)


@router.callback_query(F.data.startswith("q1_"))
async def handle_question_1(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    user = callback.from_user
    await db.record_button_click(user.id, callback.data or "q1_unknown")
    await notify_admin(
        bot,
        f"🖱 <b>Нажатие</b> · {display_name(user)} · <code>{user.id}</code>\n"
        f"{html.escape(BUTTON_LABELS.get(callback.data or '', callback.data or 'q1_unknown'))}",
    )
    await db.upsert_user(user, joined_at=None, joined_via_request=False)
    answer = {
        "q1_yes": "Да, уже зарабатываю",
        "q1_try": "Пробую, но пока слабо",
        "q1_no": "Нет, присматриваюсь",
    }.get(callback.data or "")
    if answer is None:
        await callback.answer()
        return
    await db.save_questionnaire_answer(user.id, 1, answer)
    await db.record_event(user.id, "question_answer", label="Вопрос 1", payload={"answer": answer})
    await callback.answer()
    await state.set_state(QuestionnaireState.question_2)
    try:
        await bot.send_message(user.id, await content_value("question_2_text", QUESTION_2_TEXT), reply_markup=question_2_keyboard())
    except TelegramForbiddenError:
        await db.mark_blocked(user.id)
    except TelegramAPIError:
        logger.exception("Could not send question 2 to %s", user.id)


@router.callback_query(F.data.startswith("q2_"))
async def handle_question_2(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    user = callback.from_user
    await db.record_button_click(user.id, callback.data or "q2_unknown")
    await notify_admin(
        bot,
        f"🖱 <b>Нажатие</b> · {display_name(user)} · <code>{user.id}</code>\n"
        f"{html.escape(BUTTON_LABELS.get(callback.data or '', callback.data or 'q2_unknown'))}",
    )
    answer = {
        "q2_yes": "Да, знаю",
        "q2_heard": "Слышал, но не разбирался",
        "q2_no": "Впервые слышу",
    }.get(callback.data or "")
    if answer is None:
        await callback.answer()
        return
    await db.upsert_user(user, joined_at=None, joined_via_request=False)
    await db.save_questionnaire_answer(user.id, 2, answer)
    await db.record_event(user.id, "question_answer", label="Вопрос 2", payload={"answer": answer})
    await state.clear()
    await callback.answer("Спасибо за ответы!")
    try:
        await send_checklist(bot, user.id)
    except TelegramForbiddenError:
        await db.mark_blocked(user.id)
    except TelegramAPIError:
        logger.exception("Could not send checklist to %s", user.id)


@router.callback_query(F.data == "checklist_read")
async def handle_checklist_read(callback: CallbackQuery, bot: Bot) -> None:
    await db.record_button_click(callback.from_user.id, "checklist_read")
    await db.mark_checklist_read(callback.from_user.id)
    await notify_admin(
        bot,
        f"✅ <b>Чек-лист отмечен прочитанным</b> · {display_name(callback.from_user)} · "
        f"<code>{callback.from_user.id}</code>",
    )
    await callback.answer("Отлично ✅")


@router.callback_query(F.data == "followup_interest")
async def handle_followup_interest(callback: CallbackQuery, bot: Bot) -> None:
    await db.record_button_click(callback.from_user.id, "followup_interest")
    await notify_admin(
        bot,
        f"🎓 <b>Интерес к обучению</b> · {display_name(callback.from_user)} · "
        f"<code>{callback.from_user.id}</code>",
    )
    await callback.answer("Заявка на обучение принята ✅")
    try:
        text = await content_value("followup_interest_text", FOLLOWUP_INTEREST_TEXT)
        site = await site_url()
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📞 Записаться на созвон", url=f"{site}/calls")],
                [InlineKeyboardButton(text="🌐 Открыть сайт ALW Academy", url=site)],
            ]
        )
        await bot.send_message(callback.from_user.id, text, reply_markup=keyboard)
    except TelegramForbiddenError:
        await db.mark_blocked(callback.from_user.id)
    except TelegramAPIError:
        logger.exception("Could not send follow-up interest response to %s", callback.from_user.id)


@router.callback_query(F.data == "get_lead_magnet")
async def handle_legacy_lead_button(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """Keep old welcome messages usable while users transition to the quiz."""
    await db.record_button_click(callback.from_user.id, "get_lead_magnet")
    await notify_admin(
        bot,
        f"📖 <b>Открыт старый лид-магнит</b> · {display_name(callback.from_user)} · "
        f"<code>{callback.from_user.id}</code>",
    )
    await callback.answer()
    await start_questionnaire(bot, callback.from_user, state)


@router.message(Command("stats"), F.chat.type == ChatType.PRIVATE)
async def handle_stats(message: Message) -> None:
    if not is_admin(message):
        return
    total, leads = await db.stats()
    answered, sent, read = await db.questionnaire_stats()
    await message.answer(
        f"📊 <b>Статистика ALW | Лидогенерация</b>\n\n"
        f"Приняли в канал: <b>{total}</b>\n"
        f"Завершили вопросы: <b>{answered}</b>\n"
        f"Получили чек-лист: <b>{sent}</b>\n"
        f"Нажали «Прочитал(а)»: <b>{read}</b>\n"
        f"Старый лид-магнит: <b>{leads}</b>",
    )


BUTTON_LABELS = {
    "q1_yes": "Вопрос 1: уже зарабатываю",
    "q1_try": "Вопрос 1: пробую, но слабо",
    "q1_no": "Вопрос 1: присматриваюсь",
    "q2_yes": "Вопрос 2: знаю РКО",
    "q2_heard": "Вопрос 2: слышал, но не разбирался",
    "q2_no": "Вопрос 2: впервые слышу",
    "checklist_read": "Прочитал(а) чек-лист",
    "followup_interest": "Стать учеником",
    "get_lead_magnet": "Старый лид-магнит",
}


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📚 Всё время", callback_data="admin:stats:0"),
                InlineKeyboardButton(text="📊 За 24 часа", callback_data="admin:stats:1"),
                InlineKeyboardButton(text="📈 За 7 дней", callback_data="admin:stats:7"),
            ],
            [InlineKeyboardButton(text="🗓 За 30 дней", callback_data="admin:stats:30")],
            [
                InlineKeyboardButton(text="🛰 Последние события", callback_data="admin:events"),
                InlineKeyboardButton(text="🔗 Источники", callback_data="admin:sources"),
            ],
            [
                InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:users:1"),
                InlineKeyboardButton(text="📝 Контент бота", callback_data="admin:content"),
            ],
            [InlineKeyboardButton(text="📤 Выгрузить CSV", callback_data="admin:csv")],
        ]
    )


def admin_content_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for alias in ("welcome", "question_1", "question_2", "checklist", "checklist_url", "followup_1", "followup_2", "followup_3", "followup_interest"):
        rows.append([
            InlineKeyboardButton(text=f"✏️ {alias}", callback_data=f"admin:edit:{alias}"),
            InlineKeyboardButton(text="↩️ сбросить", callback_data=f"admin:reset:{alias}"),
        ])
    rows.append([InlineKeyboardButton(text="⬅️ В панель", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def display_user_label(row: Any) -> str:
    username = row["username"] if row["username"] else None
    first_name = row["first_name"] or "Без имени"
    return html.escape(f"@{username}" if username else first_name)


def iso_since(days: int) -> str:
    return datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() - max(1, days) * 86400,
        timezone.utc,
    ).isoformat(timespec="seconds")


EVENT_LABELS = {
    "start": "Запуск бота",
    "join_request": "Заявка в канал",
    "join_approved": "Заявка одобрена",
    "button_click": "Нажатие кнопки",
    "question_answer": "Ответ на вопрос",
    "checklist_sent": "Лид-магнит отправлен",
    "followup_sent": "Дожим отправлен",
}


def event_user_label(row: Any) -> str:
    username = row["username"] if row["username"] else None
    name = f"@{username}" if username else (row["first_name"] or "Без имени")
    return html.escape(str(name))


async def admin_stats_text(days: int | None = None) -> str:
    since = iso_since(days) if days else None
    summary = await db.summary(since)
    events = await db.event_statistics(since)
    period = f"за последние {days} дн." if days else "за всё время"
    lines = [
        f"📊 <b>Панель ALW</b> · {period}",
        "",
        f"👥 Пользователи: <b>{summary['users']}</b>",
        f"✅ Заявки в канал: <b>{summary['joined']}</b>",
        f"⚡ Активные пользователи: <b>{summary['active']}</b>",
        f"🧩 Завершили анкету: <b>{summary['answered']}</b>",
        f"📖 Получили материал: <b>{summary['sent']}</b>",
        f"🛰 События: <b>{summary['events']}</b>",
        f"📖 Отметили материал: <b>{summary['read']}</b>",
        f"⛔ Заблокировали бота: <b>{summary['blocked']}</b>",
        "",
        "<b>События:</b>",
    ]
    if events:
        for event, total, users in events[:12]:
            lines.append(f"• {html.escape(EVENT_LABELS.get(event, event))}: <b>{total}</b> · людей <b>{users}</b>")
    else:
        lines.append("Пока событий нет.")
    return "\n".join(lines)


async def admin_events_text(limit: int = 20) -> str:
    rows = await db.recent_events(limit)
    lines = ["🛰 <b>Последние события</b>", ""]
    if not rows:
        lines.append("Пока событий нет.")
    for row in rows:
        stamp = html.escape(str(row["occurred_at"])[5:19].replace("T", " "))
        user = event_user_label(row) if row["user_id"] else "система"
        event = html.escape(EVENT_LABELS.get(row["event"], row["event"]))
        label = html.escape(str(row["label"] or ""))
        suffix = f" · {label}" if label else ""
        lines.append(f"<code>{stamp}</code> · {user}\n└ {event}{suffix}")
    return "\n".join(lines)


async def admin_sources_text() -> str:
    rows = await db.source_statistics()
    lines = ["🔗 <b>Источники входа</b>", "", "Для кампаний используй ссылки вида", "<code>https://t.me/бот?start=src_название</code>", ""]
    if not rows:
        lines.append("Источников пока нет.")
    else:
        for label, source, total, users in rows[:30]:
            suffix = f" <code>{html.escape(source)}</code>" if source else ""
            lines.append(f"• <b>{html.escape(label)}</b>{suffix} · входов <b>{total}</b> · людей <b>{users}</b>")
    return "\n".join(lines)


async def admin_users_text(page: int = 1) -> str:
    page = max(1, page)
    rows = await db.list_users(limit=10, offset=(page - 1) * 10)
    lines = [f"👥 <b>Пользователи · страница {page}</b>", ""]
    if not rows:
        return "\n".join(lines + ["На этой странице пользователей нет."])
    for row in rows:
        user = display_user_label(row)
        source = html.escape(str(row["source_label"] or row["source"] or "—"))
        last = html.escape(str(row["last_seen_at"] or row["joined_at"] or "—")[5:19].replace("T", " "))
        lines.append(f"<code>{row['user_id']}</code> {user}\n└ источник: {source} · активность: {last}")
    return "\n".join(lines)


def users_page_keyboard(page: int, user_ids: list[int] | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for user_id in user_ids or []:
        rows.append([InlineKeyboardButton(text=f"👤 Открыть {user_id}", callback_data=f"admin:user:{user_id}")])
    for offset, label in ((-1, "⬅️"), (1, "➡️")):
        target = max(1, page + offset)
        rows.append([InlineKeyboardButton(text=label, callback_data=f"admin:users:{target}")])
    rows.append([InlineKeyboardButton(text="⬅️ В панель", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("admin"), F.chat.type == ChatType.PRIVATE)
@router.message(Command("panel"), F.chat.type == ChatType.PRIVATE)
async def handle_admin_panel(message: Message) -> None:
    if not is_admin(message):
        return
    await message.answer(await admin_stats_text(), reply_markup=admin_panel_keyboard())


@router.callback_query(F.data == "admin:back")
async def handle_admin_back(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user):
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(await admin_stats_text(), reply_markup=admin_panel_keyboard())


@router.callback_query(F.data.startswith("admin:stats:"))
async def handle_admin_stats(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user):
        return
    try:
        days = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        days = 7
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(await admin_stats_text(days), reply_markup=admin_panel_keyboard())


@router.callback_query(F.data == "admin:events")
async def handle_admin_events(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user):
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            await admin_events_text(),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:events")],
                [InlineKeyboardButton(text="⬅️ В панель", callback_data="admin:back")],
            ]),
        )


@router.callback_query(F.data == "admin:sources")
async def handle_admin_sources(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user):
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            await admin_sources_text(),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:sources")],
                [InlineKeyboardButton(text="⬅️ В панель", callback_data="admin:back")],
            ]),
        )


@router.callback_query(F.data.startswith("admin:users:"))
async def handle_admin_users(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user):
        return
    try:
        page = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        page = 1
    await callback.answer()
    if callback.message:
        user_rows = await db.list_users(limit=10, offset=(page - 1) * 10)
        await callback.message.edit_text(
            await admin_users_text(page),
            reply_markup=users_page_keyboard(page, [int(row["user_id"]) for row in user_rows]),
        )


@router.callback_query(F.data.startswith("admin:user:"))
async def handle_admin_user_detail(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user):
        return
    try:
        user_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Некорректный пользователь", show_alert=True)
        return
    row, events = await db.detailed_user(user_id)
    if row is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    lines = [
        f"👤 <b>{display_user_label(row)}</b> · <code>{user_id}</code>",
        f"Первый вход: <code>{html.escape(str(row['first_seen_at'] or row['joined_at'] or '—'))}</code>",
        f"Последняя активность: <code>{html.escape(str(row['last_seen_at'] or '—'))}</code>",
        f"Источник: <b>{html.escape(str(row['source_label'] or row['source'] or '—'))}</b>",
        f"Payload: <code>{html.escape(str(row['start_payload'] or '—'))}</code>",
        f"Анкета: {html.escape(str(row['q1_answer'] or '—'))} / {html.escape(str(row['q2_answer'] or '—'))}",
        f"Материал: {'отправлен' if row['checklist_sent_at'] else 'не отправлен'} · "
        f"прочитан: {'да' if row['checklist_read'] else 'нет'}",
        f"Заявка в канал: {'да' if row['joined_via_request'] else 'нет'} · "
        f"заблокировал: {'да' if row['blocked'] else 'нет'}",
        "",
        "<b>Последние события:</b>",
    ]
    for event in events[:30]:
        label = html.escape(str(event["label"] or EVENT_LABELS.get(event["event"], event["event"])))
        payload = str(event["payload"] or "")
        if payload and payload != "{}":
            payload = html.escape(payload[:160])
            label += f" · <code>{payload}</code>"
        lines.append(
            f"• <code>{html.escape(str(event['occurred_at']))}</code> "
            f"{html.escape(EVENT_LABELS.get(event['event'], event['event']))} — {label}"
        )
    if not events:
        lines.append("Событий пока нет.")
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Пользователи", callback_data="admin:users:1")],
        [InlineKeyboardButton(text="← В панель", callback_data="admin:back")],
    ])
    await callback.answer()
    if callback.message:
        await callback.message.edit_text("\n".join(lines), reply_markup=markup)


@router.callback_query(F.data == "admin:content")
async def handle_admin_content_panel(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user):
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "📝 <b>Контент бота</b>\n\nВыбери сообщение, которое нужно изменить. После нажатия пришли новый текст одним сообщением.",
            reply_markup=admin_content_keyboard(),
        )


@router.callback_query(F.data.startswith("admin:edit:"))
async def handle_admin_content_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_user(callback.from_user):
        return
    alias = (callback.data or "").split(":", 2)[-1]
    if alias not in CONTENT_FIELDS:
        await callback.answer("Настройка не найдена", show_alert=True)
        return
    key, label, fallback = CONTENT_FIELDS[alias]
    current = await content_value(key, fallback)
    await state.set_state(ContentState.waiting_for_value)
    await state.update_data(content_alias=alias)
    await callback.answer("Жду новый текст")
    if callback.message:
        preview = html.escape(current[:1500])
        await callback.message.edit_text(
            f"✏️ <b>{html.escape(alias)}</b> · {html.escape(label)}\n\n"
            f"<b>Текущий текст:</b>\n<blockquote>{preview}</blockquote>\n\n"
            "Пришли новый текст следующим сообщением. Для отмены — /cancel.",
        )


@router.callback_query(F.data.startswith("admin:reset:"))
async def handle_admin_content_reset(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user):
        return
    alias = (callback.data or "").split(":", 2)[-1]
    if alias not in CONTENT_FIELDS:
        await callback.answer("Настройка не найдена", show_alert=True)
        return
    await db.delete_setting(CONTENT_FIELDS[alias][0])
    await db.record_event(callback.from_user.id, "content_reset", label=alias)
    await callback.answer("Сброшено")
    if callback.message:
        await callback.message.edit_text(
            "✅ Настройка возвращена к исходному значению.",
            reply_markup=admin_content_keyboard(),
        )


@router.callback_query(F.data == "admin:csv")
async def handle_admin_csv(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin_user(callback.from_user):
        return
    await callback.answer("Готовлю выгрузку…")
    csv_text = await db.export_users_csv()
    await bot.send_document(
        callback.from_user.id,
        BufferedInputFile(csv_text.encode("utf-8-sig"), filename="alw_users.csv"),
        caption="📄 Пользователи и события",
    )
    await bot.send_document(
        callback.from_user.id,
        BufferedInputFile((await db.export_events_csv()).encode("utf-8-sig"), filename="alw_events.csv"),
        caption="🛰 Полный журнал событий",
    )


@router.message(Command("analytics"), F.chat.type == ChatType.PRIVATE)
async def handle_analytics(message: Message) -> None:
    if not is_admin(message):
        return
    total, leads = await db.stats()
    answered, sent, read = await db.questionnaire_stats()
    button_rows = await db.button_statistics()
    lines = [
        "📈 <b>Аналитика ALW | Лидогенерация</b>",
        "",
        f"Всего пользователей: <b>{total}</b>",
        f"Завершили 2 вопроса: <b>{answered}</b>",
        f"Дошли до получения PDF: <b>{sent}</b>",
        f"Нажали «Прочитал(а)»: <b>{read}</b>",
        "",
        "<b>Нажатия callback-кнопок:</b>",
    ]
    if button_rows:
        for button, clicks, unique_users in button_rows:
            label = html.escape(BUTTON_LABELS.get(button, button))
            lines.append(f"• {label}: <b>{clicks}</b> раз, пользователей: <b>{unique_users}</b>")
    else:
        lines.append("Пока нажатий нет.")
    lines.extend([
        "",
        f"Старый лид-магнит: <b>{leads}</b>",
        "URL-кнопки Telegram не передают боту событие клика и в эту статистику не входят.",
    ])
    await message.answer("\n".join(lines))


@router.message(Command("users"), F.chat.type == ChatType.PRIVATE)
async def handle_users(message: Message) -> None:
    if not is_admin(message):
        return
    parts = (message.text or "").split()
    try:
        page = max(1, int(parts[1])) if len(parts) > 1 else 1
    except ValueError:
        page = 1
    page_size = 20
    rows = await db.list_users(limit=page_size, offset=(page - 1) * page_size)
    if not rows:
        await message.answer("Пользователей на этой странице нет.")
        return
    lines = [f"👥 <b>Пользователи — страница {page}</b>", ""]
    for row in rows:
        pdf = "PDF ✅" if row["checklist_sent_at"] else "PDF —"
        read = "прочитан ✅" if row["checklist_read"] else "не прочитан"
        joined = row["joined_at"] or "дата неизвестна"
        lines.append(
            f"<code>{row['user_id']}</code> {display_user_label(row)} — {pdf}, {read}\n"
            f"└ вход: {html.escape(str(joined))}"
        )
    lines.append("\nПодробнее: /user USER_ID · CSV: /users_csv")
    await message.answer("\n".join(lines))


@router.message(Command("user"), F.chat.type == ChatType.PRIVATE)
async def handle_user_detail(message: Message) -> None:
    if not is_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Использование: /user 123456789")
        return
    user_id = int(parts[1])
    row, clicks = await db.user_detail(user_id)
    if row is None:
        await message.answer("Пользователь не найден.")
        return
    lines = [
        f"👤 <b>Пользователь {user_id}</b>",
        f"Имя: {display_user_label(row)}",
        f"Вход: {html.escape(str(row['joined_at'] or '—'))}",
        f"Источник: {html.escape(str(row['source_label'] or row['source'] or '—'))}",
        f"Первый контакт: {html.escape(str(row['first_seen_at'] or '—'))}",
        f"Последняя активность: {html.escape(str(row['last_seen_at'] or '—'))}",
        f"Последнее событие: {html.escape(str(row['last_event'] or '—'))}",
        f"Заявка принята: {'да' if row['joined_via_request'] else 'нет'}",
        f"Вопрос 1: {html.escape(str(row['q1_answer'] or '—'))}",
        f"Вопрос 2: {html.escape(str(row['q2_answer'] or '—'))}",
        f"PDF отправлен: {'да' if row['checklist_sent_at'] else 'нет'}",
        f"PDF прочитан: {'да' if row['checklist_read'] else 'нет'}",
        "",
        "<b>Кнопки:</b>",
    ]
    if clicks:
        for button, count, last_click in clicks:
            lines.append(
                f"• {html.escape(BUTTON_LABELS.get(button, button))}: "
                f"<b>{count}</b> раз, последний: {html.escape(str(last_click or '—'))}"
            )
    else:
        lines.append("Нажатий нет.")
    await message.answer("\n".join(lines))


@router.message(Command("users_csv"), F.chat.type == ChatType.PRIVATE)
async def handle_users_csv(message: Message, bot: Bot) -> None:
    if not is_admin(message):
        return
    csv_text = await db.export_users_csv()
    document = BufferedInputFile(csv_text.encode("utf-8-sig"), filename="alw_users.csv")
    await bot.send_document(message.chat.id, document, caption="📄 Выгрузка пользователей и событий кнопок")
    await bot.send_document(
        message.chat.id,
        BufferedInputFile((await db.export_events_csv()).encode("utf-8-sig"), filename="alw_events.csv"),
        caption="🛰 Полный журнал событий",
    )


@router.message(Command("broadcast"), F.chat.type == ChatType.PRIVATE)
async def handle_broadcast_command(message: Message, state: FSMContext, bot: Bot) -> None:
    if not is_admin(message):
        return
    if message.reply_to_message is not None:
        await state.clear()
        delivered, failed = await run_broadcast(bot, message.reply_to_message)
        await message.answer(f"✅ Рассылка завершена. Доставлено: <b>{delivered}</b>, ошибок: <b>{failed}</b>")
        return
    await state.set_state(BroadcastState.waiting_for_message)
    await message.answer(
        "Пришлите следующим сообщением текст, фото, видео или кружочек.\n"
        "Для отмены используйте /cancel."
    )


@router.message(Command("cancel"), F.chat.type == ChatType.PRIVATE)
async def handle_cancel(message: Message, state: FSMContext) -> None:
    if is_admin(message):
        await state.clear()
        await message.answer("Текущее действие отменено.")


@router.message(Command("content"), F.chat.type == ChatType.PRIVATE)
async def handle_content(message: Message) -> None:
    if not is_admin(message):
        return
    lines = ["📝 <b>Управление контентом бота</b>", "", "Изменить: /set_content <ключ>"]
    for alias, (key, label, fallback) in CONTENT_FIELDS.items():
        current = await content_value(key, fallback)
        preview = " ".join(current.replace("\n", " ").split())[:140]
        lines.append(f"<b>{alias}</b> — {label}: {html.escape(preview)}")
    lines.append("\nКлючи: welcome, checklist, checklist_url, followup_1, followup_2, followup_3, followup_interest, question_1, question_2, site")
    lines.append("Для возврата к исходному тексту: /reset_content <ключ>")
    await message.answer("\n".join(lines))


@router.message(Command("set_content"), F.chat.type == ChatType.PRIVATE)
async def handle_set_content(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    alias = parts[1].strip().lower() if len(parts) > 1 else ""
    if alias not in CONTENT_FIELDS:
        await message.answer("Использование: /set_content welcome|checklist|checklist_url|followup_1|followup_2|followup_3|followup_interest|question_1|question_2|site")
        return
    await state.set_state(ContentState.waiting_for_value)
    await state.update_data(content_alias=alias)
    await message.answer(f"Пришли новым сообщением значение для <b>{alias}</b>. Можно отправить несколько строк. Для отмены — /cancel.")


@router.message(Command("reset_content"), F.chat.type == ChatType.PRIVATE)
async def handle_reset_content(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    alias = parts[1].strip().lower() if len(parts) > 1 else ""
    if alias not in CONTENT_FIELDS:
        await message.answer("Укажи ключ: welcome, checklist, checklist_url, followup_1, followup_2, followup_3, followup_interest, question_1, question_2 или site.")
        return
    key = CONTENT_FIELDS[alias][0]
    await db.delete_setting(key)
    await state.clear()
    await message.answer(f"✅ Настройка <b>{alias}</b> возвращена к исходному значению.")


@router.message(ContentState.waiting_for_value, F.chat.type == ChatType.PRIVATE)
async def handle_content_value(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    data = await state.get_data()
    alias = str(data.get("content_alias") or "")
    if alias not in CONTENT_FIELDS:
        await state.clear()
        await message.answer("Настройка не найдена. Начни заново через /content.")
        return
    value = (message.text or "").strip()
    if not value or len(value) > 8000:
        await message.answer("Нужен текст длиной от 1 до 8000 символов. Для отмены — /cancel.")
        return
    key = CONTENT_FIELDS[alias][0]
    await db.set_setting(key, value)
    await db.record_event(message.from_user.id, "content_update", label=alias)
    await state.clear()
    await message.answer(f"✅ Контент <b>{alias}</b> обновлён. Новые пользователи получат новую версию.")


@router.message(Command("set_checklist"), F.chat.type == ChatType.PRIVATE)
async def handle_set_checklist(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    await state.clear()
    await message.answer("Теперь лид-магнит отправляется ссылкой. Используй /set_content checklist_url, затем пришли новую ссылку следующим сообщением.")


@router.message(ChecklistState.waiting_for_file, F.chat.type == ChatType.PRIVATE)
async def handle_checklist_file(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    if message.document is None:
        await message.answer("Нужен именно PDF-файл. Для отмены используйте /cancel.")
        return
    await db.set_setting("checklist_file_id", message.document.file_id)
    await state.clear()
    await message.answer("✅ Чек-лист сохранён. Новые пользователи будут получать его после двух ответов.")


@router.message(BroadcastState.waiting_for_message, F.chat.type == ChatType.PRIVATE)
async def handle_broadcast_content(message: Message, state: FSMContext, bot: Bot) -> None:
    if not is_admin(message):
        return
    await state.clear()
    delivered, failed = await run_broadcast(bot, message)
    await message.answer(f"✅ Рассылка завершена. Доставлено: <b>{delivered}</b>, ошибок: <b>{failed}</b>")


@router.errors()
async def handle_error(event: ErrorEvent) -> bool:
    logger.error("Unhandled update error: %s", event.exception, exc_info=True)
    return True


async def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_BOT_TOKEN_HERE":
        raise RuntimeError("Укажите BOT_TOKEN в переменной окружения или в начале bot.py")
    if not ADMIN_ID:
        logger.warning("ADMIN_ID не задан: команды администратора будут недоступны")

    await db.connect()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    for user_id, due_at, step in await db.pending_followups():
        followup_tasks[user_id] = asyncio.create_task(followup_worker(bot, user_id, due_at, step))

    logger.info("ALW Academy bot started")
    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "chat_join_request"],
        )
    finally:
        for task in list(followup_tasks.values()):
            task.cancel()
        await bot.session.close()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
