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

FOLLOWUP_TEXT_3 = """🔥 <b>Раз ты уже зарабатываешь — посмотри на это как на второй поток.</b>

Один человек — до 43 925 ₽ только с банков. 2–3 таких клиента в месяц — это уже +100К к тому, что у тебя есть.

Это не вторая работа: банки, условия и готовые связки уже собраны на сайте ALW Academy."""

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
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()

    async def upsert_user(self, user: Any, *, joined_at: str | None, joined_via_request: bool) -> None:
        assert self.conn is not None
        await self.conn.execute(
            """INSERT INTO users (user_id, username, first_name, joined_at, joined_via_request)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 username=excluded.username,
                 first_name=excluded.first_name,
                 joined_at=COALESCE(excluded.joined_at, users.joined_at),
                 joined_via_request=MAX(users.joined_via_request, excluded.joined_via_request)""",
            (user.id, user.username, user.first_name or "", joined_at, int(joined_via_request)),
        )
        await self.conn.commit()

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
                      q1_answer, q2_answer, checklist_sent_at, checklist_read, blocked
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
                      u.joined_via_request, u.q1_answer, u.q2_answer,
                      u.checklist_sent_at, u.checklist_read, u.blocked,
                      COALESCE((SELECT COUNT(*) FROM button_clicks b WHERE b.user_id=u.user_id),0)
               FROM users u ORDER BY COALESCE(u.joined_at,'') DESC"""
        ) as cursor:
            rows = await cursor.fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "user_id", "username", "first_name", "joined_at", "joined_via_request",
            "q1_answer", "q2_answer", "checklist_sent_at", "checklist_read", "blocked",
            "button_clicks",
        ])
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


async def resolve_checklist_file() -> str | FSInputFile | None:
    value = (await db.get_setting("checklist_file_id") or CHECKLIST_FILE_PATH).strip()
    if not value:
        return None
    path = Path(value)
    return FSInputFile(path) if path.is_file() else value


async def content_value(key: str, fallback: str) -> str:
    value = await db.get_setting(key)
    return value if value is not None else fallback


async def followup_text(step: int) -> str:
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
    await schedule_followup(bot, user_id)


async def followup_worker(bot: Bot, user_id: int, due_at: float, step: int = 1) -> None:
    try:
        await asyncio.sleep(max(0.0, due_at - datetime.now(timezone.utc).timestamp()))
        text = await followup_text(step)
        await bot.send_message(user_id, text, reply_markup=await followup_keyboard(step))
        await db.mark_followup_sent(user_id)
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
    # Keep a record even if Telegram rejects a withdrawn/expired request; the
    # accepted count is updated only after approve_chat_join_request succeeds.
    await db.upsert_user(user, joined_at=None, joined_via_request=False)

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
        logger.info("Approved join request: user=%s chat=%s", user.id, request.chat.id)
    except TelegramAPIError:
        logger.exception("Could not approve join request from %s", user.id)


async def start_questionnaire(bot: Bot, user: Any, state: FSMContext) -> None:
    await state.set_state(QuestionnaireState.question_1)
    await bot.send_message(user.id, await content_value("question_1_text", QUESTION_1_TEXT), reply_markup=question_1_keyboard())


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def handle_start(message: Message, bot: Bot, state: FSMContext) -> None:
    assert message.from_user is not None
    await db.upsert_user(message.from_user, joined_at=None, joined_via_request=False)
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
    await state.clear()
    await callback.answer("Спасибо за ответы!")
    try:
        await send_checklist(bot, user.id)
    except TelegramForbiddenError:
        await db.mark_blocked(user.id)
    except TelegramAPIError:
        logger.exception("Could not send checklist to %s", user.id)


@router.callback_query(F.data == "checklist_read")
async def handle_checklist_read(callback: CallbackQuery) -> None:
    await db.record_button_click(callback.from_user.id, "checklist_read")
    await db.mark_checklist_read(callback.from_user.id)
    await callback.answer("Отлично ✅")


@router.callback_query(F.data == "followup_interest")
async def handle_followup_interest(callback: CallbackQuery, bot: Bot) -> None:
    await db.record_button_click(callback.from_user.id, "followup_interest")
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


def display_user_label(row: Any) -> str:
    username = row["username"] if row["username"] else None
    first_name = row["first_name"] or "Без имени"
    return html.escape(f"@{username}" if username else first_name)


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
