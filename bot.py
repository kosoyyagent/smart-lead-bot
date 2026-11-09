"""Telegram-бот: умные ответы + приём заявок + напоминания/рассылка.

Запуск: pip install -r requirements.txt && python bot.py
Вся логика ответов — в config.py, код трогать не нужно.
"""
import asyncio
import json
import logging
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("smart_lead_bot")

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
LEADS_FILE = os.path.join(DATA_DIR, "leads.json")
JOBS_FILE = os.path.join(DATA_DIR, "jobs.json")


# ---------- хранилище заявок (json-файл, без внешних БД) ----------

def _load(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def next_lead_id(rows):
    return (max((r.get("n", 0) for r in rows), default=0) + 1)


# ---------- умные ответы ----------

def smart_answer(text: str) -> str:
    t = text.lower()
    for key, reply in config.ANSWERS.items():
        if key in t:
            return reply
    return config.DEFAULT_ANSWER


# ---------- FSM заявки ----------

class LeadForm(StatesGroup):
    name = State()
    phone = State()
    comment = State()


bot: Bot = None  # создаётся в main(), чтобы модуль импортировался без токена
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()


@dp.message(CommandStart())
async def cmd_start(m: Message):
    await m.answer(
        "Привет! Я бот-приёмник.\n\n"
        "• Отвечаю на вопросы — просто напишите текстом.\n"
        "• Оставить заявку: /lead\n"
        "• Все команды: /help"
    )


@dp.message(Command("help"))
async def cmd_help(m: Message):
    text = "/lead — оставить заявку\n/cancel — отменить ввод заявки\n/help — эта справка"
    if m.from_user.id == config.ADMIN_ID:
        text += (
            "\n\nАдминское:\n"
            "/broadcast ЧЧ:ММ текст — рассылка всем пользователям бота в указанное время\n"
            "/jobs — список запланированных рассылок"
        )
    await m.answer(text)


@dp.message(Command("lead"))
async def cmd_lead(m: Message, state: FSMContext):
    await state.set_state(LeadForm.name)
    await m.answer(config.LEAD_ASK_NAME)


@dp.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(config.LEAD_CANCEL)


@dp.message(LeadForm.name)
async def lead_name(m: Message, state: FSMContext):
    name = (m.text or "").strip()
    if not name or len(name) > 100:
        await m.answer("Не понял имя — напишите просто текстом, например: Иван")
        return
    await state.update_data(name=name)
    await state.set_state(LeadForm.phone)
    await m.answer(config.LEAD_ASK_PHONE)


@dp.message(LeadForm.phone)
async def lead_phone(m: Message, state: FSMContext):
    phone = (m.text or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 10:
        await m.answer("Похоже, номер короткий. Напишите телефон полностью, например +7 900 123-45-67:")
        return
    await state.update_data(phone=phone)
    await state.set_state(LeadForm.comment)
    await m.answer(config.LEAD_ASK_COMMENT)


@dp.message(LeadForm.comment)
async def lead_comment(m: Message, state: FSMContext):
    data = await state.get_data()
    comment = (m.text or "").strip() or "-"
    rows = _load(LEADS_FILE)
    n = next_lead_id(rows)
    rows.append({
        "n": n,
        "name": data.get("name", "-"),
        "phone": data.get("phone", "-"),
        "comment": comment,
        "user_id": m.from_user.id,
        "username": m.from_user.username or "-",
        "ts": datetime.now().isoformat(timespec="seconds"),
    })
    _save(LEADS_FILE, rows)
    await state.clear()
    await m.answer(config.LEAD_DONE.format(n=n))
    if config.ADMIN_ID:
        try:
            await bot.send_message(
                config.ADMIN_ID,
                config.LEAD_ADMIN_TPL.format(
                    n=n,
                    name=data.get("name", "-"),
                    phone=data.get("phone", "-"),
                    comment=comment,
                    username=m.from_user.username or "-",
                    user_id=m.from_user.id,
                ),
            )
        except Exception as e:
            log.warning("admin notify failed: %s", e)


# ---------- рассылка по расписанию (админ) ----------

def _known_users() -> set:
    users = set()
    for r in _load(LEADS_FILE):
        if r.get("user_id"):
            users.add(r["user_id"])
    return users


async def _do_broadcast(text: str):
    sent = 0
    for uid in _known_users():
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception as e:
            log.info("broadcast skip %s: %s", uid, e)
    log.info("broadcast done, sent=%s", sent)
    if config.ADMIN_ID:
        try:
            await bot.send_message(config.ADMIN_ID, f"Рассылка выполнена, доставлено: {sent}")
        except Exception:
            pass


@dp.message(Command("broadcast"))
async def cmd_broadcast(m: Message):
    if m.from_user.id != config.ADMIN_ID:
        await m.answer("Команда только для администратора.")
        return
    parts = (m.text or "").split(maxsplit=2)
    if len(parts) < 3 or ":" not in parts[1]:
        await m.answer("Формат: /broadcast ЧЧ:ММ текст рассылки")
        return
    hh, mm = parts[1].split(":", 1)
    try:
        hh, mm = int(hh), int(mm)
        assert 0 <= hh < 24 and 0 <= mm < 60
    except Exception:
        await m.answer("Время в формате ЧЧ:ММ, например /broadcast 18:30 Текст")
        return
    text = parts[2]
    now = datetime.now()
    run_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if run_at <= now:
        run_at = run_at.replace(day=now.day)  # если время уже прошло — на завтра
        from datetime import timedelta
        run_at += timedelta(days=1)
    job = scheduler.add_job(_do_broadcast, "date", run_date=run_at, args=[text])
    jobs = _load(JOBS_FILE)
    jobs.append({"id": job.id, "at": run_at.isoformat(timespec="seconds"), "text": text})
    _save(JOBS_FILE, jobs)
    await m.answer(f"Рассылка запланирована на {run_at:%d.%m %H:%M}.")


@dp.message(Command("jobs"))
async def cmd_jobs(m: Message):
    if m.from_user.id != config.ADMIN_ID:
        await m.answer("Команда только для администратора.")
        return
    jobs = _load(JOBS_FILE)
    if not jobs:
        await m.answer("Запланированных рассылок нет.")
        return
    lines = [f"{j['at']} — {j['text'][:60]}" for j in jobs]
    await m.answer("Запланировано:\n" + "\n".join(lines))


# ---------- умные ответы на всё остальное ----------

@dp.message(F.text)
async def fallback(m: Message):
    await m.answer(smart_answer(m.text))


async def main():
    global bot
    if not config.BOT_TOKEN or "PASTE" in config.BOT_TOKEN:
        raise SystemExit("Впишите токен в config.py (BOT_TOKEN) и ADMIN_ID")
    bot = Bot(config.BOT_TOKEN)
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
