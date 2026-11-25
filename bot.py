import asyncio, hashlib, hmac, json, math, os, random, sqlite3, time
from contextlib import contextmanager
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery, WebAppInfo
from aiohttp import web

# ---------- НАСТРОЙКИ ----------
BOT_TOKEN = "9060753040:AAHLBIg43U7UrZbzF8QFbwHEnmGnAlJ66xo"

# ВСТАВЬ СЮДА ССЫЛКУ ОТ FADEHOST (когда он её выдаст)
WEB_APP_URL = "https://my-casino-bot.fadehost.app" 

PROXY_URL = None 
DEV = False

bot = Bot(token=BOT_TOKEN, session=AiohttpSession(proxy=PROXY_URL) if PROXY_URL else None)
dp = Dispatcher()
BOT_USERNAME = ""

# ---------- БАЗА ----------
@contextmanager
def tx():
    c = sqlite3.connect("test.db", timeout=20)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()

def init_db():
    with tx() as c:
        c.execute("CREATE TABLE IF NOT EXISTS u (id INTEGER PRIMARY KEY, b INTEGER, nickname TEXT)")
        cols = [r[1] for r in c.execute("PRAGMA table_info(u)")]
        for name, default in {"spins": 0, "seen": 0, "daily": 0, "lvl": 1, "en": 100, "en_t": 0, "mine_t": 0, "donated": 0}.items():
            if name not in cols:
                c.execute(f"ALTER TABLE u ADD COLUMN {name} INTEGER DEFAULT {default}")
        
        c.execute("""CREATE TABLE IF NOT EXISTS ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            uid INTEGER, 
            text TEXT, 
            time INTEGER
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS idea_votes (
            uid INTEGER, 
            idea_id INTEGER, 
            PRIMARY KEY (uid, idea_id)
        )""")

def touch(user):
    uid = int(user["id"])
    name = (user.get("first_name") or "Player")[:15]
    with tx() as c:
        c.execute("INSERT OR IGNORE INTO u(id,b,nickname,spins,seen,daily) VALUES(?,1000,?,0,0,0)", (uid, name))
        c.execute("UPDATE u SET seen=? WHERE id=?", (int(time.time()), uid))
    return uid

def settle(uid, bet, payout, **extra):
    with tx() as c:
        ok = c.execute("UPDATE u SET b=b-?+?, spins=spins+1 WHERE id=? AND b>=?", (bet, payout, uid, bet)).rowcount == 1
        bal = c.execute("SELECT b FROM u WHERE id=?", (uid,)).fetchone()["b"]
    if not ok:
        return {"error": "❌ Не хватает монет"}
    return {"bet": bet, "payout": payout, "balance": bal, **extra}

# ---------- АВТОРИЗАЦИЯ ----------
def auth(req):
    raw = req.headers.get("X-Init", "")
    if DEV and not raw:
        return {"id": 1, "first_name": "Dev"}
    data = dict(parse_qsl(raw))
    got = data.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    if time.time() - int(data.get("auth_date", 0)) > 86400:
        raise web.HTTPUnauthorized()
    if not got or not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), got):
        raise web.HTTPUnauthorized()
    return json.loads(data["user"])

def api(fn):
    async def wrapper(req):
        try:
            user = auth(req)
            body = await req.json() if req.method == "POST" and req.can_read_body else {}
            uid = touch(user)
            return web.json_response(fn(uid, body, req))
        except web.HTTPUnauthorized:
            return web.json_response({"error": "Авторизация истекла."}, status=401)
        except Exception as e:
            print(f"ОШИБКА API: {e}")
            return web.json_response({"error": f"Сбой сервера: {str(e)}"}, status=500)
    return wrapper

# ---------- ИГРЫ ----------
SECTORS = [0, 2, 0, 0.5, 0, 3, 0, 1.5]

def wheel(uid, d, _):
    bet = int(d.get("bet", 0))
    if bet not in (10, 50, 100, 500):
        return {"error": "Неверная ставка"}
    i = random.randrange(len(SECTORS))
    return settle(uid, bet, int(bet * SECTORS[i]), sector=i, mult=SECTORS[i])

DART_ZONES = {"center": (3, 0.20, 0.0, 0.13), "ring": (2, 0.45, 0.40, 0.65)}

def darts(uid, d, _):
    z = DART_ZONES.get(d.get("zone"))
    if not z:
        return {"error": "Неверная зона"}
    mult, chance, r0, r1 = z
    hit = random.random() < chance
    r = random.uniform(r0, r1) if hit else random.uniform(0.7, 1.1)
    a = random.uniform(0, 2 * math.pi)
    return settle(uid, 50, int(50 * mult) if hit else 0, hit=hit, x=r * math.cos(a), y=r * math.sin(a))

def basket(uid, d, _):
    hit = random.random() < 0.4
    return settle(uid, 30, 60 if hit else 0, hit=hit)

def football(uid, d, _):
    side = int(d["side"])
    if side not in (0, 1, 2):
        return {"error": "Неверный угол"}
    keeper = random.randrange(3)
    goal = keeper != side
    return settle(uid, 30, 42 if goal else 0, goal=goal, keeper=keeper)

# ---------- ПРОФИЛЬ / БОНУС / РЕЙТИНГ ----------
def profile(uid, d, _):
    with tx() as c:
        r = c.execute("SELECT b,nickname,spins,daily FROM u WHERE id=?", (uid,)).fetchone()
    return {"balance": r["b"], "nickname": r["nickname"], "spins": r["spins"],
            "daily_in": max(0, int(r["daily"] + 86400 - time.time())),
            "ref": f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"}

def nickname(uid, d, _):
    nick = str(d.get("nickname", "")).strip()[:15]
    if not nick:
        return {"error": "Пустой ник"}
    with tx() as c:
        c.execute("UPDATE u SET nickname=? WHERE id=?", (nick, uid))
    return {"success": True, "nickname": nick}

def daily(uid, d, _):
    with tx() as c:
        last = c.execute("SELECT daily FROM u WHERE id=?", (uid,)).fetchone()["daily"]
        if time.time() - last < 86400:
            return {"error": "Бонус ещё не готов"}
        c.execute("UPDATE u SET b=b+500, daily=? WHERE id=?", (int(time.time()), uid))
        bal = c.execute("SELECT b FROM u WHERE id=?", (uid,)).fetchone()["b"]
    return {"bet": 0, "payout": 500, "balance": bal}

def rating(uid, d, req):
    cat = req.query.get("cat", "rich")
    args = ()
    if cat == "spins":
        val, where = "spins", "1=1"
    elif cat == "online":
        val, where, args = "b", "seen>=?", (int(time.time()) - 300,)
    else:
        val, where = "b", "1=1"
    with tx() as c:
        rows = c.execute(f"SELECT id,nickname,donated,{val} v FROM u WHERE {where} ORDER BY v DESC LIMIT 100", args).fetchall()
        mine = c.execute(f"SELECT {val} v FROM u WHERE id=?", (uid,)).fetchone()["v"]
        rank = c.execute(f"SELECT COUNT(*)+1 n FROM u WHERE {where} AND {val}>?", args + (mine,)).fetchone()["n"]
        online = c.execute("SELECT COUNT(*) n FROM u WHERE seen>=?", (int(time.time()) - 300,)).fetchone()["n"]
    return {"top": [{"nickname": r["nickname"], "v": r["v"], "me": r["id"] == uid, "vip": r["donated"] > 0} for r in rows],
            "me": {"rank": rank, "v": mine}, "online": online}

# ---------- ШАХТА ----------
def mine_row(c, uid):
    r = c.execute("SELECT lvl,en,en_t,mine_t FROM u WHERE id=?", (uid,)).fetchone()
    now = time.time()
    mt = r["mine_t"] or now
    return r["lvl"], min(100.0, r["en"] + (now - r["en_t"])), mt, int(min(10800, now - mt) * r["lvl"] / 60), now

def balance(c, uid):
    return c.execute("SELECT b FROM u WHERE id=?", (uid,)).fetchone()["b"]

def mine(uid, d, _):
    with tx() as c:
        lvl, en, mt, pend, now = mine_row(c, uid)
        c.execute("UPDATE u SET mine_t=? WHERE id=?", (mt, uid))
    return {"lvl": lvl, "energy": en, "pending": pend, "cost": 250 * lvl * lvl}

def tap(uid, d, _):
    n = max(0, min(int(d.get("n", 0)), 60))
    with tx() as c:
        lvl, en, mt, pend, now = mine_row(c, uid)
        k = min(n, int(en)); earn = k * lvl
        c.execute("UPDATE u SET b=b+?, en=?, en_t=? WHERE id=?", (earn, en - k, now, uid))
        bal = balance(c, uid)
    return {"earned": earn, "energy": en - k, "balance": bal}

def collect(uid, d, _):
    with tx() as c:
        lvl, en, mt, pend, now = mine_row(c, uid)
        if pend < 1:
            return {"error": "Пока пусто — шахта ещё копает"}
        c.execute("UPDATE u SET b=b+?, mine_t=? WHERE id=?", (pend, now, uid))
        bal = balance(c, uid)
    return {"bet": 0, "payout": pend, "balance": bal}

def upgrade(uid, d, _):
    with tx() as c:
        lvl, en, mt, pend, now = mine_row(c, uid)
        cost = 250 * lvl * lvl
        ok = c.execute("UPDATE u SET b=b-?+?, lvl=lvl+1, mine_t=? WHERE id=? AND b+?>=?", (cost, pend, now, uid, pend, cost)).rowcount == 1
        if not ok:
            return {"error": "❌ Не хватает монет"}
        bal = balance(c, uid)
    return {"balance": bal, "lvl": lvl + 1}

# ---------- ИДЕИ ----------
def get_ideas(uid, d, _):
    with tx() as c:
        rows = c.execute("""
            SELECT i.id, i.text, u.nickname, 
                   (SELECT COUNT(*) FROM idea_votes WHERE idea_id = i.id) as likes,
                   (SELECT COUNT(*) FROM idea_votes WHERE idea_id = i.id AND uid = ?) as voted
            FROM ideas i
            JOIN u ON i.uid = u.id
            ORDER BY likes DESC, i.time DESC
            LIMIT 50
        """, (uid,)).fetchall()
    return [{"id": r["id"], "text": r["text"], "nickname": r["nickname"], "likes": r["likes"], "voted": r["voted"] > 0} for r in rows]

def add_idea(uid, d, _):
    text = str(d.get("text", "")).strip()[:100]
    if len(text) < 5:
        return {"error": "Слишком короткая идея"}
    with tx() as c:
        last = c.execute("SELECT time FROM ideas WHERE uid = ? ORDER BY time DESC LIMIT 1", (uid,)).fetchone()
        if last and time.time() - last["time"] < 86400:
            return {"error": "Ты уже предлагал идею сегодня. Подожди!"}
        c.execute("INSERT INTO ideas (uid, text, time) VALUES (?, ?, ?)", (uid, text, int(time.time())))
    return {"success": True}

def like_idea(uid, d, _):
    idea_id = int(d.get("idea_id", 0))
    with tx() as c:
        if not c.execute("SELECT 1 FROM ideas WHERE id=?", (idea_id,)).fetchone():
            return {"error": "Идея не найдена"}
        voted = c.execute("SELECT 1 FROM idea_votes WHERE uid=? AND idea_id=?", (uid, idea_id)).fetchone()
        if voted:
            c.execute("DELETE FROM idea_votes WHERE uid=? AND idea_id=?", (uid, idea_id))
            res = False
        else:
            c.execute("INSERT INTO idea_votes (uid, idea_id) VALUES (?, ?)", (uid, idea_id))
            res = True
    return {"success": True, "liked": res}

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
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print("СЕРВЕР ЗАПУЩЕН!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
