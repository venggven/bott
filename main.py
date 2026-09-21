from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

from app.content import DISCLAIMER, OFFER, QUESTIONS, REMINDER, WELCOME, result_for_score
from app.database import Database

router = Router()
db: Database


def one_button(text: str, callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=callback_data)]]
    )


def question_keyboard(index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"answer:{index}:{points}")]
            for label, points in QUESTIONS[index].answers
        ]
    )


async def send_question(message: Message, index: int) -> None:
    question = QUESTIONS[index]
    await message.answer(
        f"<b>Вопрос {index + 1} из {len(QUESTIONS)}</b>\n\n{question.text}",
        reply_markup=question_keyboard(index),
    )


@router.message(CommandStart())
async def start(message: Message) -> None:
    if not message.from_user:
        return
    await db.start_quiz(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )
    await message.answer(WELCOME, reply_markup=one_button("Начать тест", "begin"))


@router.callback_query(F.data == "begin")
async def begin(callback: CallbackQuery) -> None:
    if not callback.message or not callback.from_user:
        return
    state = await db.get_state(callback.from_user.id)
    if not state or state.status != "active":
        await callback.answer("Нажми /start, чтобы начать заново", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await send_question(callback.message, state.question_index)


@router.callback_query(F.data.startswith("answer:"))
async def answer(callback: CallbackQuery) -> None:
    if not callback.data or not callback.message:
        return
    try:
        _, raw_index, raw_points = callback.data.split(":")
        index, points = int(raw_index), int(raw_points)
    except (ValueError, TypeError):
        await callback.answer("Некорректный ответ", show_alert=True)
        return
    if index not in range(len(QUESTIONS)) or points not in (1, 2, 3):
        await callback.answer("Некорректный ответ", show_alert=True)
        return

    state = await db.add_answer(callback.from_user.id, index, points)
    if state is None:
        await callback.answer("Этот ответ уже учтен")
        return

    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    if state.question_index < len(QUESTIONS):
        await send_question(callback.message, state.question_index)
        return

    result = result_for_score(state.score)
    await db.complete_quiz(callback.from_user.id, result.zone)
    await callback.message.answer(
        f"{result.text}\n\n{DISCLAIMER}",
        reply_markup=one_button(result.button, "show_offer"),
    )


@router.callback_query(F.data == "show_offer")
async def show_offer(callback: CallbackQuery) -> None:
    if not callback.message:
        return
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        OFFER,
        reply_markup=one_button("Забрать доступ за 1 490 ₽", "checkout"),
    )


@router.callback_query(F.data == "checkout")
async def checkout(callback: CallbackQuery) -> None:
    if not callback.message:
        return
    await db.mark_checkout_started(callback.from_user.id)
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "🧪 <b>Тестовый режим</b>\n\n"
        "Оплата пока не подключена. Деньги не списываются — ты просто дошла "
        "до финального шага воронки.\n\n"
        "Чтобы пройти тест заново, отправь /start.",
    )


async def reminder_worker(bot: Bot, interval: int) -> None:
    while True:
        try:
            for user_id in await db.reminder_candidates():
                try:
                    await bot.send_message(
                        user_id,
                        REMINDER,
                        reply_markup=one_button("Забрать уроки за 1 490 ₽", "checkout"),
                    )
                except TelegramForbiddenError:
                    logging.info("User %s blocked the bot", user_id)
                finally:
                    await db.mark_reminder_sent(user_id)
        except Exception:
            logging.exception("Reminder worker failed")
        await asyncio.sleep(interval)


async def main() -> None:
    global db
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is missing in .env")

    logging.basicConfig(level=logging.INFO)
    db = Database(os.getenv("DATABASE_PATH", "bot.sqlite3"))
    await db.init()

    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    interval = max(10, int(os.getenv("REMINDER_CHECK_SECONDS", "60")))
    reminder_task = asyncio.create_task(reminder_worker(bot, interval))
    try:
        await dispatcher.start_polling(bot)
    finally:
        reminder_task.cancel()
        with suppress(asyncio.CancelledError):
            await reminder_task
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
