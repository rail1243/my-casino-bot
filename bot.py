import asyncio, hashlib, hmac, json, math, os, random, sqlite3, time
from contextlib import contextmanager
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery, WebAppInfo
from aiohttp import web

# ---------- НАСТРОЙКИ ----------
# ВСТАВЬ СВОЙ ТОКЕН (потом смени его в BotFather, так как он засветился в чате)
BOT_TOKEN = "9060753040:AAHLBIg43U7UrZbzF8QFbwHEnmGnAlJ66xo"

# ССЫЛКУ СЮДА ВСТАВИШЬ ПОСЛЕ ДЕПЛОЯ (Render выдаст постоянный адрес)
WEB_APP_URL = "https://example.com" 

# На хостинге VPN НЕ НУЖЕН
PROXY_URL = None 
DEV = False

bot = Bot(token=BOT_TOKEN, session=AiohttpSession(proxy=PROXY_URL) if PROXY_URL else None)
dp = Dispatcher()
BOT_USERNAME = ""

# ... (база данных, игры, профиль, шахта, идеи — БЕЗ ИЗМЕНЕНИЙ, как в прошлом коде) ...

# ---------- БОТ ----------
@dp.message(Command("start"))
async def start(msg: Message, command: CommandObject):
    uid = msg.from_user.id
    with tx() as c:
        new = c.execute("SELECT 1 FROM u WHERE id=?", (uid,)).fetchone() is None
    touch({"id": uid, "first_name": msg.from_user.first_name})
    arg = command.args or ""
    if new and arg.startswith("ref_") and arg[4:].isdigit() and int(arg[4:]) != uid:
        with tx() as c:
            if c.execute("UPDATE u SET b=b+500 WHERE id=?", (int(arg[4:]),)).rowcount:
                c.execute("UPDATE u SET b=b+500 WHERE id=?", (uid,))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎮 ОТКРЫТЬ NEMESIS OS", web_app=WebAppInfo(url=WEB_APP_URL))]])
    await msg.answer("Система готова. Нажми кнопку ниже:", reply_markup=kb)

async def home(req):
    return web.FileResponse("./index.html")

app = web.Application()
app.router.add_get("/", home)
for path, fn in {"profile": profile, "rating": rating, "mine": mine, "ideas": get_ideas}.items():
    app.router.add_get(f"/api/{path}", api(fn))
for path, fn in {"wheel": wheel, "darts": darts, "basket": basket, "football": football,
                 "nickname": nickname, "daily": daily, "tap": tap, "collect": collect, "upgrade": upgrade,
                 "add_idea": add_idea, "like_idea": like_idea}.items():
    app.router.add_post(f"/api/{path}", api(fn))

# ---------- ДОНАТ ----------
@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@dp.message(F.successful_payment)
async def paid(msg: Message):
    stars = msg.successful_payment.total_amount
    touch({"id": msg.from_user.id, "first_name": msg.from_user.first_name})
    with tx() as c:
        c.execute("UPDATE u SET b=b+?, donated=donated+? WHERE id=?", (stars * 20, stars, msg.from_user.id))
    await msg.answer(f"❤️ Спасибо за {stars} ⭐! Начислено {stars * 20} 🪙 и значок ⭐ в рейтинге.")

@dp.message(Command("paysupport"))
async def paysupport(msg: Message):
    await msg.answer("По вопросам платежей и возвратов напиши владельцу бота.")

async def donate(req):
    user = auth(req)
    amount = int((await req.json()).get("amount", 0))
    if amount not in (50, 100, 250, 500):
        return web.json_response({"error": "Неверная сумма"})
    link = await bot.create_invoice_link(
        title="Поддержка NEMESIS OS", description=f"Донат {amount} ⭐ · бонус {amount * 20} монет и значок ⭐ в рейтинге",
        payload=f"don:{user['id']}", provider_token="", currency="XTR", prices=[LabeledPrice(label="Донат", amount=amount)])
    return web.json_response({"link": link})

app.router.add_post("/api/donate", donate)

async def main():
    init_db()
    global BOT_USERNAME
    BOT_USERNAME = (await bot.get_me()).username
    runner = web.AppRunner(app)
    await runner.setup()
    # Хостинг сам сообщает, на каком порту работать
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print("СЕРВЕР ЗАПУЩЕН!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())