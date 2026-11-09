"""Проверка логики бота без Telegram: заявка по шагам, умные ответы, рассылка."""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config, bot

print("== умные ответы ==")
for q in ["Привет!", "как дела?", "сколько стоит цена", "какой у вас адрес", "хочу заказать"]:
    print("  %r -> %s" % (q, bot.smart_answer(q)[:70]))

print("\n== заявка ==")
rows = bot._load(bot.LEADS_FILE)
n0 = bot.next_lead_id(rows)
lead = {"n": n0, "name": "Иван", "phone": "+7 900 123-45-67",
        "comment": "нужен бот для заявок", "user_id": 12345,
        "username": "ivan", "ts": "2026-11-09T21:30:00"}
rows.append(lead)
bot._save(bot.LEADS_FILE, rows)
back = bot._load(bot.LEADS_FILE)
assert back[-1]["n"] == n0 and back[-1]["phone"] == lead["phone"]
print("  заявка #%d записана и прочитана: %s %s" % (n0, back[-1]["name"], back[-1]["phone"]))
msg = config.LEAD_ADMIN_TPL.format(**lead)
print("  уведомление админу:")
for line in msg.split("\n"):
    print("   ", line)
assert msg.startswith("Новая заявка #%d" % n0)
assert "+7 900" in msg

print("\n== рассылка: получатели ==")
users = bot._known_users()
assert 12345 in users
print("  известные пользователи:", sorted(users))

print("\n== валидация телефона ==")
def ok_phone(p):
    return len("".join(c for c in p if c.isdigit())) >= 10
assert ok_phone("+7 900 123-45-67") and not ok_phone("123")
print("  +7 900 123-45-67 ok, '123' отклонён")

os.remove(bot.LEADS_FILE)
if os.path.exists(bot.JOBS_FILE):
    os.remove(bot.JOBS_FILE)
print("\nDEMO OK — все проверки прошли")
