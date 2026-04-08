import asyncio
import random
import httpx
import logging
import signal
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ── Logging Setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
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
API_TOKEN    = '8770224655:AAHwmeIEnu0IPusu8XzumEhmMOLGcCGqMs0'
SUPABASE_URL = 'https://basniiolppmtpzishhtn.supabase.co'.rstrip('/')
SUPABASE_KEY = ('eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJhc25paW9scHBtdHB6aXNoaHRuIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTQ3NjMwOCwiZXhwIjoyMDkxMDUyMzA4fQ.qrj1BO5dNilRDvgKtvTdwIWjBhFTRyGzuHPD271Xcac')

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
            # Recreate the event to avoid stale state from previous rounds
            engine.round_over_event = asyncio.Event()
            logger.info(f"Round started for chat {chat_id}")

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
            logger.info(f"Round ended for chat {chat_id} with {len(engine.scores)} players")

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
        logger.info(f"Game loop cancelled for chat {chat_id}")


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
            f"⚡ *CRATE DROP!* The crates descend from the sky!\n"
            f"🎁 Wonder what you need to do...",
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
    if not event.user.id:
        return
    chat_id = event.chat.id
    engine  = get_or_create_engine(chat_id)
    if (
        engine.crate_drop_message_id == event.message_id
        and engine.crates_dropping > 0
        and event.user.id not in [c['user_id'] for c in engine.crate_claimers]
        and len(engine.crate_claimers) < 3
    ):
        engine.crate_claimers.append({'user_id': event.user.id, 'username': ''})


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
        return

    u_id = str(message.from_user.id)
    if not get_user(u_id):
        await message.answer(
            "🃏 *GameMaster:* \"You have nothing. You ARE nothing. Register first.\"",
            parse_mode="Markdown"
        )
        return

    inventory = get_inventory(u_id)
    if not inventory:
        await message.answer(
            "🃏 *GameMaster:* \"Your inventory is empty. How *pathetic*.\"",
            parse_mode="Markdown"
        )
        return

    keyboard = []
    for item in inventory:
        item_id   = item.get("id")
        itype     = item.get("type", "").lower()
        xp_reward = item.get("xp_reward", 0)

        if "wood"   in itype and "crate" in itype: label = f"🪵 WOOD CRATE ({xp_reward} XP)";   cb = f"open_{item_id}"
        elif "bronze" in itype and "crate" in itype: label = f"🥉 BRONZE CRATE ({xp_reward} XP)"; cb = f"open_{item_id}"
        elif "iron"   in itype and "crate" in itype: label = f"⚙️ IRON CRATE ({xp_reward} XP)";   cb = f"open_{item_id}"
        elif "super"  in itype and "crate" in itype: label = f"🎁 SUPER CRATE ({xp_reward} XP)";  cb = f"open_{item_id}"
        elif itype == "shield":    label = "🛡️ SHIELD [LOCKED]";           cb = f"use_{item_id}"
        elif itype == "teleport":  label = "🌀 TELEPORT (Choose Sector)";   cb = f"teleport_{item_id}"
        elif "multiplier" in itype:
            mult = item.get("multiplier_value", 2)
            kind = "XP" if "xp" in itype else "SILVER"
            label = f"⚡ {kind} MULTIPLIER x{mult}"
            cb = f"use_{item_id}"
        elif "locked_" in itype:   label = "🔒 LEGENDARY ITEM [TOO POWERFUL]"; cb = f"info_{item_id}"
        else:                      label = f"❓ {itype.upper()}";               cb = f"use_{item_id}"

        keyboard.append([InlineKeyboardButton(text=label, callback_data=cb)])

    profile = get_profile(u_id)
    used    = profile['inventory_count'] if profile else len(inventory)
    total   = profile['backpack_slots']  if profile else 5

    await message.answer(
        f"📦 *YOUR INVENTORY*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📊 Slots: {used}/{total} — tap any item to use it\n\n"
        f"*Your Items:*",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="Markdown"
    )


@dp.message(F.text == "!claims")
async def show_claims(message: types.Message):
    if message.chat.type != "private":
        await message.answer(_dm_only_group_reply("!claims"), parse_mode="Markdown")
        return

    u_id = str(message.from_user.id)
    if not get_user(u_id):
        await message.answer(
            "🃏 *GameMaster:* \"No account. No claims. Register first.\"",
            parse_mode="Markdown"
        )
        return

    unclaimed = get_unclaimed_items(u_id)
    if not unclaimed:
        await message.answer(
            "🃏 *GameMaster:* \"You have no unclaimed rewards. "
            "Go earn something for once.\"",
            parse_mode="Markdown"
        )
        return

    locked_names = {
        "locked_legendary_artifact": "⚔️ LEGENDARY ARTIFACT",
        "locked_mythical_crown":     "👑 MYTHICAL CROWN",
        "locked_void_stone":         "🌑 VOID STONE",
        "locked_eternal_flame":      "🔥 ETERNAL FLAME",
        "locked_celestial_key":      "🗝️ CELESTIAL KEY",
    }
    item_labels = {
        "xp_multiplier":    lambda m: f"⚡ XP MULTIPLIER x{m}",
        "silver_multiplier": lambda m: f"💎 SILVER MULTIPLIER x{m}",
        "super_crate":  lambda _: "🎁 SUPER CRATE",
        "wood_crate":   lambda _: "🪵 WOOD CRATE",
        "bronze_crate": lambda _: "🥉 BRONZE CRATE",
        "iron_crate":   lambda _: "⚙️ IRON CRATE",
        "shield":       lambda _: "🛡️ SHIELD",
        "teleport":     lambda _: "🌀 TELEPORT",
    }

    keyboard = []
    for item in unclaimed:
        itype   = item.get("type", "").lower()
        mult    = item.get("multiplier_value", 0)
        item_id = item.get("id")
        if "locked_" in itype:
            lbl  = locked_names.get(itype, "🔒 LEGENDARY ITEM")
            text = f"{lbl} [CLAIM]"
        else:
            fn   = item_labels.get(itype, lambda _: f"🎁 {itype.upper()}")
            text = f"{fn(mult)} [CLAIM]"
        keyboard.append([InlineKeyboardButton(text=text, callback_data=f"claim_{item_id}")])

    await message.answer(
        f"🎁 *UNCLAIMED REWARDS*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ {len(unclaimed)} item(s) waiting!\n"
        f"💡 Tap *[CLAIM]* to move to inventory\n"
        f"❌ Don't leave them — they may expire!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="Markdown"
    )


# ── Claim callback ────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("claim_"))
async def claim_item_callback(query: types.CallbackQuery):
    u_id = str(query.from_user.id)
    if not get_user(u_id):
        await query.answer("Your account was lost. Please restart with !tutorial", show_alert=True)
        return
    try:
        item_id = int(query.data.split("_")[1])
    except (IndexError, ValueError):
        await query.answer("Invalid item.", show_alert=True)
        return

    ok, msg = claim_item(u_id, item_id)
    if ok:
        await query.answer(f"✅ {msg}")
        remaining = get_unclaimed_items(u_id)
        if not remaining:
            await query.message.edit_text(
                "🃏 *GameMaster:* \"All claimed. Good little minion.\"",
                parse_mode="Markdown"
            )
        else:
            await query.message.edit_text(
                f"🎁 *UNCLAIMED REWARDS*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Remaining: *{len(remaining)}* item(s) — send `!claims` to refresh.",
                parse_mode="Markdown"
            )
    else:
        await query.answer(f"❌ {msg}", show_alert=True)


# ── Crate / item callbacks ────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("open_"))
async def open_crate_callback(callback: types.CallbackQuery):
    import re
    m = re.search(r'\d+', callback.data)
    if m:
        await _open_crate(callback.message, str(callback.from_user.id), int(m.group()))
    await callback.answer()


@dp.callback_query(F.data.startswith("use_"))
async def use_item_callback(callback: types.CallbackQuery):
    import re
    m = re.search(r'\d+', callback.data)
    if m:
        await _use_item(callback.message, str(callback.from_user.id), int(m.group()))
    await callback.answer()


@dp.callback_query(F.data.startswith("info_"))
async def info_item_callback(callback: types.CallbackQuery):
    await callback.answer(
        "🔒 This item is too powerful to use right now. "
        "Upgrade your backpack to unlock its potential.",
        show_alert=True
    )


async def _open_crate(message: types.Message, user_id: str, item_id: int):
    if not get_user(user_id):
        await message.answer("🃏 *GameMaster:* \"You don't exist. Register first.\"", parse_mode="Markdown")
        return
    inventory = get_inventory(user_id)
    crate     = next((it for it in inventory if it.get("id") == item_id), None)
    if not crate:
        await message.answer("🃏 *GameMaster:* \"Invalid crate.\"", parse_mode="Markdown")
        return
    if "crate" not in crate.get("type", "").lower():
        await message.answer("🃏 *GameMaster:* \"That's not a crate. Learn to read.\"", parse_mode="Markdown")
        return

    xp        = crate.get("xp_reward", 0)
    ctype     = crate.get("type", "unknown").replace("_", " ").upper()
    add_xp(user_id, xp)
    remove_inventory_item(user_id, item_id)
    await message.answer(f"✨ *CRATE OPENED!*\n📦 {ctype}\n+{xp} XP", parse_mode="Markdown")


async def _use_item(message: types.Message, user_id: str, item_id: int):
    if not get_user(user_id):
        await message.answer("🃏 *GameMaster:* \"You don't exist. Register first.\"", parse_mode="Markdown")
        return
    inventory = get_inventory(user_id)
    item      = next((it for it in inventory if it.get("id") == item_id), None)
    if not item:
        await message.answer("🃏 *GameMaster:* \"Invalid item.\"", parse_mode="Markdown")
        return
    itype = item.get("type", "").lower()
    if "crate" in itype:
        await message.answer("🃏 *GameMaster:* \"That's a crate. Use the OPEN button.\"", parse_mode="Markdown")
    elif itype == "shield":
        await message.answer(
            "🃏 *GameMaster:* \"Shield mechanics are still being forged. "
            "Sit tight, impatient one.\"",
            parse_mode="Markdown"
        )
    elif "locked_" in itype:
        await message.answer(
            "🃏 *GameMaster:* \"You can't USE that. It would destroy you. "
            "Upgrade your backpack first.\"",
            parse_mode="Markdown"
        )
    else:
        await message.answer("🃏 *GameMaster:* \"Unknown item. Even I don't know what this is.\"", parse_mode="Markdown")


# ── Teleport callbacks ────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("teleport_to_"))
async def teleport_destination(callback: types.CallbackQuery):
    import re
    m = re.search(r'\d+', callback.data)
    if not m:
        return
    sector_id = int(m.group())
    u_id      = str(callback.from_user.id)
    if not get_user(u_id):
        await callback.answer("Your account was lost. Restart with !tutorial", show_alert=True)
        return
    if sector_id < 1 or sector_id > 9:
        await callback.answer("That sector is locked!", show_alert=True)
        return

    all_sectors = load_sectors()
    info        = all_sectors.get(sector_id, {})
    sname       = info.get("name", f"Sector {sector_id}") if info else f"Sector {sector_id}"
    senv        = info.get("environment", "") if info else ""
    sperks      = info.get("perks", "") if info else ""

    set_sector(u_id, sector_id)
    inventory = get_inventory(u_id)
    for it in inventory:
        if it.get("type", "").lower() == "teleport":
            remove_inventory_item(u_id, it.get("id"))
            break

    await callback.answer("✨ Teleported!")
    await callback.message.edit_text(
        f"✨ *TELEPORTATION COMPLETE*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📍 Arrived at: *#{sector_id} {sname.upper()}*\n"
        + (f"🌍 {senv}\n"       if senv   else "")
        + (f"⚡ Perks: {sperks}\n" if sperks else "")
        + "\nYour teleport has been consumed.",
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("teleport_"))
async def teleport_item_callback(callback: types.CallbackQuery):
    # Only fires for teleport_{item_id} (NOT teleport_to_...)
    # because teleport_to_ is caught by the handler above first
    import re
    m = re.search(r'\d+', callback.data)
    if not m:
        return
    item_id = int(m.group())
    u_id    = str(callback.from_user.id)
    if not get_user(u_id):
        await callback.answer("Your account was lost. Restart with !tutorial", show_alert=True)
        return
    inventory = get_inventory(u_id)
    item      = next((it for it in inventory if it.get("id") == item_id), None)
    if not item or item.get("type", "").lower() != "teleport":
        await callback.answer("Invalid teleport item.", show_alert=True)
        return

    await callback.answer("Choose your destination!")
    all_sectors = load_sectors()
    keyboard    = []
    for sid in range(1, 10):
        info  = all_sectors.get(sid, {})
        sname = info.get("name", f"Sector {sid}") if info else f"Sector {sid}"
        perks = info.get("perks", "") if info else ""
        btxt  = f"#{sid} {sname}" + (f"  {perks}" if perks else "")
        keyboard.append([InlineKeyboardButton(text=btxt, callback_data=f"teleport_to_{sid}")])
    keyboard.append([InlineKeyboardButton(
        text="🔒 Sectors 10-64 (LOCKED — Level up to unlock)",
        callback_data="locked_sectors"
    )])
    await callback.message.answer(
        "🌀 *TELEPORT NETWORK*\n━━━━━━━━━━━━━━━\nChoose your sector:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@dp.callback_query(F.data == "locked_sectors")
async def locked_sectors_info(callback: types.CallbackQuery):
    await callback.answer("Sectors 10-64 unlock as you level up!", show_alert=True)


# ═══════════════════════════════════════════════════════════════════════════
#  MISC COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

@dp.message(F.text == "!help")
async def show_help(message: types.Message):
    await message.answer(get_help_message(), parse_mode="Markdown")


@dp.message(F.text == "!shop")
async def show_shop(message: types.Message):
    await message.answer(
        "🃏 *GameMaster:* \"The shop is still under construction. "
        "Patience, you impatient worm.\"",
        parse_mode="Markdown"
    )


@dp.message(F.text == "!upgrade")
async def upgrade_backpack_cmd(message: types.Message):
    await message.answer(
        "🃏 *GameMaster:* \"The Queen's Satchel upgrade is not ready yet.\n\n"
        "When it launches: 5 → 20 slots for 900 Naira.\n\n"
        "For now, manage your measly 5 slots and stop complaining.\"",
        parse_mode="Markdown"
    )


@dp.message(F.text.startswith("!changename"))
async def change_name(message: types.Message):
    if message.chat.type != "private":
        await message.answer(_dm_only_group_reply("!changename"), parse_mode="Markdown")
        return
    u_id = str(message.from_user.id)
    user = get_user(u_id)
    if not user:
        await message.answer(
            "🃏 *GameMaster:* \"You're not registered. "
            "You can't change a name you don't have.\"",
            parse_mode="Markdown"
        )
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("🃏 *GameMaster:* \"Usage: `!changename NewName`\"", parse_mode="Markdown")
        return
    new_name = parts[1].strip()[:20]
    old_name = user.get("username", message.from_user.first_name)
    if new_name.lower() == old_name.lower():
        await message.answer(
            f"🃏 *GameMaster:* \"You're already '{old_name}'. Changing nothing. As usual.\"",
            parse_mode="Markdown"
        )
        return
    user["username"] = new_name
    save_user(u_id, user)
    await message.answer(
        f"✅ Name changed: *{old_name}* → *{new_name}*\n"
        f"🃏 *GameMaster:* \"Running from your past? Noted.\"",
        parse_mode="Markdown"
    )


@dp.message(F.text == "!tutorial")
async def trigger_tutorial(message: types.Message, state: FSMContext):
    if message.chat.type != "private":
        await message.answer(_dm_only_group_reply("!tutorial"), parse_mode="Markdown")
        return
    from initiation import Trial
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ I'm ready",    callback_data="trial_yes")],
        [InlineKeyboardButton(text="🚪 Never mind", callback_data="trial_no")],
    ])
    await message.answer(
        "🃏 *GameMaster:* \"So you want to relive the trials. How... *entertaining*.\"",
        parse_mode="Markdown",
        reply_markup=markup
    )
    await state.set_state(Trial.awaiting_username)


@dp.message(F.text == "!start")
async def manual_start(message: types.Message, state: FSMContext):
    if message.chat.type != "private":
        await message.answer("🃏 *GameMaster:* \"Message me privately, fool.\"", parse_mode="Markdown")
        return
    u_id = str(message.from_user.id)
    if get_user(u_id):
        await message.answer(
            "🃏 *GameMaster:* \"You're already registered. Stop pestering me.\"",
            parse_mode="Markdown"
        )
        return
    from initiation import Trial
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ I'm ready to enter", callback_data="trial_yes")],
        [InlineKeyboardButton(text="🚪 I'm just lost",       callback_data="trial_no")],
    ])
    await message.answer(
        "🃏 *GameMaster:* \"Well, well, well. Look what crawled into my domain.\"\n\n"
        "\"You show up unannounced, uninvited, and probably unprepared. "
        "I don't know who you are. I don't care who you are.\"\n\n"
        "\"But something about you... *interests* me. "
        "Are you here to join *The 64*? Or did you just wander in by accident?\"",
        parse_mode="Markdown",
        reply_markup=markup
    )
    await state.set_state(Trial.awaiting_username)


# ── Text-command versions of open/use ─────────────────────────────────────

@dp.message(F.text.regexp(r"^!open\s+\d+$"))
async def crate_open_handler(message: types.Message):
    if message.chat.type != "private":
        await message.answer("🃏 *GameMaster:* \"Open crates in *private*, not here.\"", parse_mode="Markdown")
        return
    import re
    m = re.search(r'\d+', message.text)
    if m:
        pos       = int(m.group()) - 1
        inventory = get_inventory(str(message.from_user.id))
        if 0 <= pos < len(inventory):
            await _open_crate(message, str(message.from_user.id), inventory[pos]["id"])


@dp.message(F.text.regexp(r"^!use\s+\d+$"))
async def use_item_handler(message: types.Message):
    if message.chat.type != "private":
        await message.answer("🃏 *GameMaster:* \"Use items in *private*, fool.\"", parse_mode="Markdown")
        return
    import re
    m = re.search(r'\d+', message.text)
    if m:
        pos       = int(m.group()) - 1
        inventory = get_inventory(str(message.from_user.id))
        if 0 <= pos < len(inventory):
            await _use_item(message, str(message.from_user.id), inventory[pos]["id"])


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    logger.info("Starting bot...")
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Graceful shutdown on signals
    loop = asyncio.get_event_loop()
    
    async def shutdown_signal():
        logger.info("Shutdown signal received, stopping bot gracefully...")
        await dp.fsm.storage.close()
        loop.stop()
    
    # Register signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(shutdown_signal())
        )
    
    try:
        logger.info("Bot polling started")
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Bot interrupted by user")
    except Exception as e:
        logger.error(f"Bot error: {e}", exc_info=True)
    finally:
        logger.info("Bot stopped")

if __name__ == "__main__":
    asyncio.run(main())
