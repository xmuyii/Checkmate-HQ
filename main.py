import asyncio
import random
import httpx
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import (
    get_user, register_user,
    add_points, get_weekly_leaderboard, get_alltime_leaderboard,
    add_silver, set_sector, upgrade_backpack,
    get_inventory, get_profile,
    add_xp, use_xp, use_silver,
    remove_inventory_item, load_sectors, get_sector_display,
    save_user, calculate_level, check_level_up,
    add_unclaimed_item, get_unclaimed_items,
    claim_item, remove_unclaimed_item,
    award_powerful_locked_item, add_inventory_item,
)
from fusion_handlers import word_fusion_router
from initiation import initiation_router

# ── Config ────────────────────────────────────────────────────────────────
API_TOKEN    = '8770224655:AAHYPEb_VeX2Xpr80emmD5FIhFKdCkYfCMA'
SUPABASE_URL = 'https://basniiolppmtpzishhtn.supabase.co'.rstrip('/')
SUPABASE_KEY = ('eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.'
                'eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJhc25paW9scHBtdHB6aXNoaHRuIiwicm9sZSI6'
                'InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTQ3NjMwOCwiZXhwIjoyMDkxMDUyMzA4fQ.'
                'qrj1BO5dNilRDvgKtvTdwIWjBhFTRyGzuHPD271Xcac')

bot = Bot(token=API_TOKEN)
dp  = Dispatcher()

# Routers are included FIRST so their handlers run before dp's generic ones
dp.include_router(initiation_router)
dp.include_router(word_fusion_router)


# ═══════════════════════════════════════════════════════════════════════════
#  GAME ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class GameEngine:
    def __init__(self):
        self.active         = False   # round currently live
        self.running        = False   # game loop running
        self.word1          = ""
        self.word2          = ""
        self.letters        = ""      # combined lowercase
        self.scores         = {}      # {user_id: {pts, name, user_id, leveled_up}}
        self.used_words     = []
        self.round_duration = 120     # seconds
        self.empty_rounds   = 0
        self.message_count  = 0
        self.games_played   = 0
        self.games_until_help = random.randint(3, 7)
        self.crates_dropping  = 0
        self.crate_claimers   = []
        self.crate_drop_message_id = None
        # Set this event to cut a round short (used by !forcerestart)
        self.round_over_event = asyncio.Event()

active_games: dict[int, GameEngine] = {}

def get_or_create_engine(chat_id: int) -> GameEngine:
    if chat_id not in active_games:
        active_games[chat_id] = GameEngine()
    return active_games[chat_id]


# ═══════════════════════════════════════════════════════════════════════════
#  SUPABASE HELPERS
# ═══════════════════════════════════════════════════════════════════════════

async def fetch_supabase_words():
    headers = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
    url = f"{SUPABASE_URL}/rest/v1/Dictionary?word_length=eq.7&select=word&limit=1"
    async with httpx.AsyncClient() as client:
        try:
            r1 = await client.get(f"{url}&offset={random.randint(0, 500)}", headers=headers, timeout=8.0)
            r2 = await client.get(f"{url}&offset={random.randint(0, 500)}", headers=headers, timeout=8.0)
            w1 = r1.json()[0]['word'].upper() if r1.json() else "PLAYERS"
            w2 = r2.json()[0]['word'].upper() if r2.json() else "DANGERS"
            return w1, w2
        except Exception:
            return "PLAYERS", "DANGERS"

def is_anagram(guess: str, letters_pool: str) -> bool:
    pool = list(letters_pool)
    for ch in guess:
        if ch in pool:
            pool.remove(ch)
        else:
            return False
    return True

async def check_supabase_dict(word: str) -> bool:
    headers = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/Dictionary?word=eq.{word}&select=word",
                headers=headers, timeout=8.0
            )
            return len(r.json()) > 0
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════════════════
#  HELP TEXT
# ═══════════════════════════════════════════════════════════════════════════

def get_help_message() -> str:
    return (
        "🃏 *GameMaster:* \"Oh, look who's struggling already.\"\n\n"
        "*COMMANDS*\n"
        "`!fusion` — Start the game _(group only)_\n"
        "`!forcerestart` — Force-end current round _(group, admins only)_\n"
        "`!weekly` — Weekly leaderboard\n"
        "`!alltime` — All-time leaderboard\n"
        "`!profile` — Your stats _(DM only)_\n"
        "`!inventory` — Your items _(DM only)_\n"
        "`!claims` — Unclaimed rewards _(DM only)_\n"
        "`!changename Name` — Change your name _(DM only)_\n"
        "`!tutorial` — Replay the tutorial _(DM only)_\n"
        "`!help` — This message\n\n"
        "*HOW TO PLAY*\n"
        "1️⃣ Two 7-letter words appear in the group.\n"
        "2️⃣ Type any real word you can form from their combined letters.\n"
        "3️⃣ Points = word length − 2. No duplicate words per round.\n"
        "4️⃣ Round lasts 2 minutes, then a new one starts automatically.\n"
        "5️⃣ Top 3 per round earn bonus crates!\n\n"
        "*PROGRESSION*\n"
        "⭐ 1 XP per point · 📊 Level up every 100 XP\n"
        "🎁 Level-ups give crates & multipliers\n"
        "⚡ 20 % chance of a mid-round crate drop\n"
        "📊 Weekly reset every Sunday 00:00 UTC"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  CORE GAME LOOP
# ═══════════════════════════════════════════════════════════════════════════

async def run_auto_harvest(chat_id: int):
    """
    Main game loop.  Runs continuous 2-minute rounds for one chat.
    Stops after 3 consecutive empty rounds.
    """
    engine = get_or_create_engine(chat_id)
    engine.running     = True
    engine.empty_rounds = 0

    try:
        while engine.running:
            # ── Reset round state ────────────────────────────────────────
            engine.scores       = {}
            engine.used_words   = []
            engine.message_count = 0
            engine.active       = True
            engine.round_over_event.clear()

            engine.word1, engine.word2 = await fetch_supabase_words()
            engine.letters = (engine.word1 + engine.word2).lower()

            # Random crate drop
            crate_note = ""
            if random.random() < 0.2:
                engine.crates_dropping = random.randint(1, 2)
                engine.crate_claimers  = []
                crate_note = f"\n\n🎁 *BONUS:* {engine.crates_dropping} crate(s) will drop mid-round!"
            else:
                engine.crates_dropping = 0

            await bot.send_message(
                chat_id,
                f"🃏 *GameMaster:* \"New round. Try not to starve.\"\n\n"
                f"🎯 `{engine.word1}`  `{engine.word2}`"
                f"{crate_note}\n\n"
                f"⏱️ You have *2 minutes*. Go.",
                parse_mode="Markdown"
            )

            # ── Timed round (wait_for enforces the deadline) ─────────────
            try:
                await asyncio.wait_for(
                    _round_timer(chat_id, engine),
                    timeout=engine.round_duration
                )
            except asyncio.TimeoutError:
                pass  # normal — time's up

            # ── Round is over ────────────────────────────────────────────
            engine.active = False

            sorted_scores = sorted(
                engine.scores.values(), key=lambda x: x['pts'], reverse=True
            )

            lead = "🏆 *ROUND OVER*\n━━━━━━━━━━━━━━━\n"

            if not sorted_scores:
                lead += "Nobody scored. Pathetic."
                engine.empty_rounds += 1
            else:
                engine.empty_rounds = 0

                if engine.crates_dropping > 0 and engine.crate_claimers:
                    for claimer in engine.crate_claimers:
                        add_unclaimed_item(str(claimer['user_id']), "super_crate", 1)
                    lead += f"🎁 {len(engine.crate_claimers)} player(s) claimed crates!\n\n"

                for i, p in enumerate(sorted_scores):
                    medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i + 1}."
                    lead += f"{medal} {p['name']} — {p['pts']} pts\n"
                    if i < 3:
                        add_unclaimed_item(p['user_id'], "super_crate", 1)

            lead += "\n📊 `!weekly` | `!alltime` for full stats"
            await bot.send_message(chat_id, lead, parse_mode="Markdown")

            # ── Level-up announcements ───────────────────────────────────
            for uid, sd in engine.scores.items():
                if sd.get("leveled_up"):
                    user = get_user(uid)
                    if user:
                        lvl = user.get('level', 1)
                        msg = (
                            f"🎊 *LEVEL UP!* {sd['name']} reached *LEVEL {lvl}*!\n\n"
                            f"🃏 *GameMaster:* \"Managed not to embarrass yourself. "
                            f"Here's your pathetic reward.\"\n\n"
                            f"✨ Use `!claims` in DM to collect your bonus items."
                        )
                        add_unclaimed_item(uid, "super_crate", 1)
                        if random.random() < 0.5:
                            add_unclaimed_item(uid, "xp_multiplier", 1, multiplier_value=2)
                        else:
                            add_unclaimed_item(uid, "silver_multiplier", 1, multiplier_value=2)
                        if lvl % 5 == 0:
                            iname, idesc = award_powerful_locked_item(uid)
                            msg += (
                                f"\n\n⚡ *MILESTONE!* Unlocked: *{iname}*\n"
                                f"_{idesc}_\n"
                                f"⚠️ Too powerful to use until you upgrade your backpack."
                            )
                        await bot.send_message(chat_id, msg, parse_mode="Markdown")

            # ── Dormancy ─────────────────────────────────────────────────
            if engine.empty_rounds >= 3:
                engine.running = False
                engine.active  = False
                await bot.send_message(
                    chat_id,
                    "🃏 *GameMaster:* \"Silence. I'm bored. "
                    "Type `!fusion` when you actually want to play.\"",
                    parse_mode="Markdown"
                )
                break

            # ── Periodic help message ────────────────────────────────────
            engine.games_played += 1
            if engine.games_played >= engine.games_until_help:
                await bot.send_message(chat_id, get_help_message(), parse_mode="Markdown")
                engine.games_until_help = engine.games_played + random.randint(3, 7)

            # ── Break between rounds ─────────────────────────────────────
            await asyncio.sleep(15)

    except asyncio.CancelledError:
        engine.active  = False
        engine.running = False


async def _round_timer(chat_id: int, engine: GameEngine):
    """
    Async timer for one round.  Signals mid-round events.
    Returns early if engine.round_over_event is set (force-restart).
    """
    if engine.crates_dropping > 0:
        # Wait 50 s then drop the crate
        try:
            await asyncio.wait_for(engine.round_over_event.wait(), timeout=50)
            return
        except asyncio.TimeoutError:
            pass

        crate_msg = await bot.send_message(
            chat_id,
            "⚡ *CRATE DROP!* React to this message — first 3 get a Super Crate!",
            parse_mode="Markdown"
        )
        engine.crate_drop_message_id = crate_msg.message_id
        engine.crate_claimers = []

        try:
            await asyncio.wait_for(engine.round_over_event.wait(), timeout=70)
            return
        except asyncio.TimeoutError:
            pass
    else:
        # 60-second warning at midpoint
        try:
            await asyncio.wait_for(engine.round_over_event.wait(), timeout=60)
            return
        except asyncio.TimeoutError:
            pass

        await bot.send_message(
            chat_id,
            "⏱️ *GameMaster:* \"One minute left. Still pathetic, but there's time.\"",
            parse_mode="Markdown"
        )

        try:
            await asyncio.wait_for(engine.round_over_event.wait(), timeout=60)
            return
        except asyncio.TimeoutError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
#  SADISTIC UNREGISTERED REPLY
# ═══════════════════════════════════════════════════════════════════════════

def _unreg() -> str:
    return random.choice([
        "🃏 *GameMaster:* \"A ghost? I don't deal with ghosts. "
        "Message me *privately* so I can register your pathetic soul first.\"",
        "🃏 *GameMaster:* \"Who are you? Nobody. "
        "Come to my DMs and prove you exist before wasting my time.\"",
        "🃏 *GameMaster:* \"Unregistered souls are invisible to me. "
        "Slide into my DMs. Beg. Register. Then come back.\"",
    ])


# ═══════════════════════════════════════════════════════════════════════════
#  GROUP COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

@dp.message(F.text == "!fusion")
async def start_game(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer(
            "🃏 *GameMaster:* \"This is a GROUP game, fool. "
            "Stop pestering me in private about it.\"",
            parse_mode="Markdown"
        )
        return

    chat_id = message.chat.id
    engine  = get_or_create_engine(chat_id)

    if engine.running:
        await message.answer(
            "🃏 *GameMaster:* \"The souls are already being harvested. Open your eyes.\"",
            parse_mode="Markdown"
        )
        return

    u_id = str(message.from_user.id)
    if not get_user(u_id):
        await message.answer(
            "🃏 *GameMaster:* \"Someone triggered my game without even registering. "
            "Bold. Stupid. Message me privately to join — but fine, I'll start anyway.\"\n\n"
            "_(Message me in DM to register your soul!)_",
            parse_mode="Markdown"
        )

    asyncio.create_task(run_auto_harvest(chat_id))


@dp.message(F.text == "!forcerestart")
async def force_restart(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer(
            "🃏 *GameMaster:* \"Use this in the group, fool.\"",
            parse_mode="Markdown"
        )
        return

    # Admins only
    try:
        member = await message.chat.get_member(message.from_user.id)
        if member.status not in ["administrator", "creator"]:
            await message.reply(
                "🃏 *GameMaster:* \"You think YOU can restart MY game? "
                "Admins only, little peon.\"",
                parse_mode="Markdown"
            )
            return
    except Exception:
        pass  # If we can't check, allow it

    chat_id = message.chat.id
    engine  = get_or_create_engine(chat_id)

    if not engine.running:
        await message.answer(
            "🃏 *GameMaster:* \"Nothing is running. "
            "You can't restart what doesn't exist. Type `!fusion` to start.\"",
            parse_mode="Markdown"
        )
        return

    engine.round_over_event.set()
    engine.active = False

    await message.answer(
        "🃏 *GameMaster:* \"Fine. FINE. Round terminated. "
        "Fresh words incoming. This better not be a habit.\"",
        parse_mode="Markdown"
    )


@dp.message_reaction()
async def on_message_reaction(event: types.MessageReactionUpdated):
    if not event.user_id:
        return
    chat_id = event.chat.id
    engine  = get_or_create_engine(chat_id)
    if (
        engine.crate_drop_message_id == event.message_id
        and engine.crates_dropping > 0
        and event.user_id not in [c['user_id'] for c in engine.crate_claimers]
        and len(engine.crate_claimers) < 3
    ):
        engine.crate_claimers.append({'user_id': event.user_id, 'username': ''})


# ═══════════════════════════════════════════════════════════════════════════
#  GROUP MESSAGE HANDLER  (word guesses)
# ═══════════════════════════════════════════════════════════════════════════

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def on_group_message(message: types.Message):
    if not message.text:
        return

    text = message.text.strip()

    # Ignore all bot commands — they have dedicated handlers above
    if text.startswith("!"):
        return

    chat_id = message.chat.id
    engine  = get_or_create_engine(chat_id)
    u_id    = str(message.from_user.id)

    # ── Unregistered player ───────────────────────────────────────────────
    user = get_user(u_id)
    if not user:
        if random.random() < 0.25:
            await message.reply(_unreg(), parse_mode="Markdown")
        return

    # ── Word-repeat nudge every 4 messages during active round ───────────
    if engine.active:
        engine.message_count += 1
        if engine.message_count >= 4:
            engine.message_count = 0
            await message.answer(
                f"📌 *Still playing:* `{engine.word1}`  `{engine.word2}`",
                parse_mode="Markdown"
            )

    # ── Stale guess (round not active) ───────────────────────────────────
    if not engine.active:
        guess = text.lower()
        if len(guess) >= 3 and engine.letters and is_anagram(guess, engine.letters):
            await message.reply(
                "🛑 *GameMaster:* \"Round is OVER. "
                "Type `!fusion` to start a new one.\"",
                parse_mode="Markdown"
            )
        return

    # ── Validate guess ────────────────────────────────────────────────────
    guess = text.lower()

    if len(guess) < 3:
        return

    if guess in engine.used_words:
        await message.reply(f"❌ `{guess.upper()}` was already guessed this round!")
        return

    if not is_anagram(guess, engine.letters):
        return  # silently ignore

    if await check_supabase_dict(guess):
        pts = max(len(guess) - 2, 1)
        engine.used_words.append(guess)

        db_name = user.get("username", message.from_user.first_name)
        add_points(u_id, pts, db_name)
        add_xp(u_id, pts)
        old_lvl, new_lvl = check_level_up(u_id)

        if u_id not in engine.scores:
            engine.scores[u_id] = {"pts": 0, "name": db_name, "user_id": u_id, "leveled_up": False}
        engine.scores[u_id]["pts"] += pts

        feedback = f"✅ `{guess.upper()}` +{pts} pts  ⭐ +{pts} XP"
        if old_lvl and new_lvl:
            feedback += f"\n🎊 *LEVEL UP!* {old_lvl} → {new_lvl}"
            engine.scores[u_id]["leveled_up"] = True

        await message.reply(feedback, parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════════════════
#  LEADERBOARDS
# ═══════════════════════════════════════════════════════════════════════════

@dp.message(F.text == "!weekly")
async def show_weekly(message: types.Message):
    if not get_user(str(message.from_user.id)):
        await message.answer(_unreg(), parse_mode="Markdown")
        return
    lb   = get_weekly_leaderboard()
    text = "🏆 *WEEKLY LEADERBOARD*\n━━━━━━━━━━━━━━━\n"
    if not lb:
        text += "No scores yet. Shocking."
    else:
        for i, p in enumerate(lb, 1):
            medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"{i}."
            text += f"{medal} {p['username']} — {p['points']} pts\n"
    await message.answer(text, parse_mode="Markdown")


@dp.message(F.text == "!alltime")
async def show_alltime(message: types.Message):
    if not get_user(str(message.from_user.id)):
        await message.answer(_unreg(), parse_mode="Markdown")
        return
    lb   = get_alltime_leaderboard()
    text = "🏆 *ALL-TIME LEADERBOARD*\n━━━━━━━━━━━━━━━\n"
    if not lb:
        text += "Blank. Just like your future."
    else:
        for i, p in enumerate(lb, 1):
            medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"{i}."
            text += f"{medal} {p['username']} — {p['points']} pts\n"
    await message.answer(text, parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════════════════
#  DM-ONLY COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

def _dm_only_group_reply(cmd: str) -> str:
    return random.choice([
        f"🃏 *GameMaster:* \"Did you just try to use `{cmd}` *in public*? "
        "I don't expose private information to the masses. DM me, fool.\"",
        f"🃏 *GameMaster:* \"Oh how embarrassing. `{cmd}` is for *private* use. "
        "Message me directly, you absolute amateur.\"",
        f"🃏 *GameMaster:* \"`{cmd}` in the group chat? Really? "
        "Come to my DMs and handle your personal business there.\"",
    ])


@dp.message(F.text.startswith("!profile"))
async def show_profile(message: types.Message):
    if message.chat.type != "private":
        await message.answer(_dm_only_group_reply("!profile"), parse_mode="Markdown")
        return

    parts = message.text.split()
    if len(parts) > 1:
        await message.answer(
            f"🃏 *GameMaster:* \"Why are you snooping on *{parts[1]}*? "
            "You can only view YOUR own profile here.\"",
            parse_mode="Markdown"
        )
        return

    u_id    = str(message.from_user.id)
    profile = get_profile(u_id)
    if not profile:
        await message.answer(
            "🃏 *GameMaster:* \"You have no profile. "
            "Complete the tutorial first.\"",
            parse_mode="Markdown"
        )
        return

    bar_len = 20
    filled  = int((profile['xp_progress'] / profile['xp_needed']) * bar_len) \
              if profile['xp_needed'] > 0 else 0
    xp_bar  = f"{'█' * filled}{'░' * (bar_len - filled)}"

    await message.answer(
        f"🃏 *GameMaster:* \"So you want to stare at your own reflection. Fine.\"\n\n"
        f"👤 *PROFILE: {profile['username']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎖️ *LEVEL {profile['level']}*\n"
        f"⭐ XP: {profile['xp']} | [{xp_bar}] {profile['xp_progress']}/{profile['xp_needed']}\n\n"
        f"💰 Silver: {profile['silver']}\n"
        f"📍 Sector: {profile['sector_display']}\n\n"
        f"📊 *STATS*\n"
        f"├─ Weekly Points: {profile['weekly_points']}\n"
        f"└─ All-Time Points: {profile['all_time_points']}\n\n"
        f"📦 *INVENTORY*\n"
        f"├─ Claimed: {profile['inventory_count']}/{profile['backpack_slots']} slots\n"
        f"├─ Unclaimed: {profile['unclaimed_count']} items ⚠️\n"
        f"└─ Crates: {profile['crate_count']} | Shields: {profile['shield_count']}",
        parse_mode="Markdown"
    )


@dp.message(F.text == "!inventory")
async def show_inventory(message: types.Message):
    if message.chat.type != "private":
        await message.answer(_dm_only_group_reply("!inventory"), parse_mode="Markdown")
   
