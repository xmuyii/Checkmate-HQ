import asyncio
import random
import httpx
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import (
    get_user,
    register_user,
    add_points,
    get_weekly_leaderboard,
    get_alltime_leaderboard,
    add_silver,
    set_sector,
    upgrade_backpack,
    get_inventory,
    get_profile,
    add_xp,
    use_xp,
    use_silver,
    remove_inventory_item,
    load_sectors,
    get_sector_display,
    save_user,
    calculate_level,
    check_level_up,
    add_unclaimed_item,
    get_unclaimed_items,
    claim_item,
    remove_unclaimed_item,
    award_powerful_locked_item,
    add_inventory_item
)
from fusion_handlers import word_fusion_router
from initiation import initiation_router

# --- CONFIG ---
API_TOKEN = '8770224655:AAFAHmPzdwCRbi5Y7VfO6cCBpuHZdT_dI2I'
SUPABASE_URL = 'https://basniiolppmtpzishhtn.supabase.co'.rstrip('/')
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJhc25paW9scHBtdHB6aXNoaHRuIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTQ3NjMwOCwiZXhwIjoyMDkxMDUyMzA4fQ.qrj1BO5dNilRDvgKtvTdwIWjBhFTRyGzuHPD271Xcac'

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
dp.include_router(initiation_router)
dp.include_router(word_fusion_router)


# ─────────────────────────────────────────────
#  GAME ENGINE
# ─────────────────────────────────────────────

class GameEngine:
    def __init__(self):
        self.active = False          # Is a round currently live?
        self.running = False         # Is the harvest loop running?
        self.word1 = ""
        self.word2 = ""
        self.letters = ""
        self.scores = {}
        self.used_words = []
        self.round_duration = 120    # 2 minutes per round
        self.empty_rounds = 0
        self.message_count = 0
        self.games_played = 0
        self.games_until_help = random.randint(3, 7)
        self.crates_dropping = 0
        self.crate_claimers = []
        self.crate_drop_message_id = None
        # This event fires when a round ends (so the loop can move on)
        self.round_over_event = asyncio.Event()

# One engine per group chat
active_games: dict[int, GameEngine] = {}

def get_or_create_engine(chat_id: int) -> GameEngine:
    if chat_id not in active_games:
        active_games[chat_id] = GameEngine()
    return active_games[chat_id]


# ─────────────────────────────────────────────
#  SUPABASE HELPERS
# ─────────────────────────────────────────────

async def fetch_supabase_words():
    headers = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
    url = f"{SUPABASE_URL}/rest/v1/Dictionary?word_length=eq.7&select=word&limit=1"
    async with httpx.AsyncClient() as client:
        try:
            r1 = await client.get(f"{url}&offset={random.randint(0, 500)}", headers=headers)
            r2 = await client.get(f"{url}&offset={random.randint(0, 500)}", headers=headers)
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
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/Dictionary?word=eq.{word}&select=word",
            headers=headers
        )
        return len(r.json()) > 0


# ─────────────────────────────────────────────
#  HELP TEXT
# ─────────────────────────────────────────────

def get_help_message() -> str:
    return """🃏 *GameMaster:* \"Oh, look who's struggling already. Couldn't even find your way huh?.\"

*COMMANDS* _(if your tiny brain can manage it)_
`!fusion` — Start the game. Shocking, I know.
`!changename NewName` — Change your username 
`!tutorial` — Restart the tutorial 
`!profile` — Check your stats 
`!inventory` — View claimed items 
`!claims` — Check unclaimed rewards ⚠️
`!weekly` — This week's leaderboard
`!alltime` — All-time leaderboard. Spoiler: it's not you.
`!help` — This message

*PROGRESSION*
⭐ 1 XP per point earned
📊 Level up every 100 XP
🎁 Every level-up gives FREE super crates
⚡ 20% chance crates drop mid-round since you're are all so bad at this game, 
    better get lucky

*HOW TO PLAY*
1️⃣ Two words WILL appear. Try not to panic.
2️⃣ Type ANY word you can form from their combined letters.
3️⃣ Points = Do I still need to explain this?
4️⃣ Duplicates in THIS round are ignored.
5️⃣ Round lasts a while, then resets.

🏆 Top 3 per round win bonus crates!
📊 Weekly reset every Sunday 00:00 UTC"""


# ─────────────────────────────────────────────
#  CORE GAME LOOP
# ─────────────────────────────────────────────

async def run_auto_harvest(message: types.Message, chat_id: int):
    """
    The main game loop for a chat.
    Runs rounds back-to-back until 3 consecutive empty rounds.
    Each round is exactly `engine.round_duration` seconds long,
    enforced via asyncio.wait_for so it always ends on time.
    """
    engine = get_or_create_engine(chat_id)
    engine.running = True
    engine.empty_rounds = 0

    try:
        while engine.running:
            # ── Reset for new round ──────────────────────────
            engine.scores = {}
            engine.used_words = []
            engine.message_count = 0
            engine.active = True
            engine.round_over_event.clear()

            engine.word1, engine.word2 = await fetch_supabase_words()
            engine.letters = (engine.word1 + engine.word2).lower()

            # Random crate drop (20 % chance)
            crate_msg_text = ""
            if random.random() < 0.2:
                num_crates = random.randint(1, 2)
                crate_msg_text = (
                    f"\n\n🎁 *BONUS:* {num_crates} CRATE(S) might drop mid-round!"
                )
                engine.crates_dropping = num_crates
                engine.crate_claimers = []
            else:
                engine.crates_dropping = 0

            await bot.send_message(
                chat_id,
                f"🃏 *GameMaster:* \"New round. Try not to starve.\"\n\n"
                f"🎯 `{engine.word1}`  `{engine.word2}`"
                f"{crate_msg_text}\n\n"
                f"⏱️ You have *2 minutes*. Go.",
                parse_mode="Markdown"
            )

            # ── Run the timed round ───────────────────────────
            try:
                await asyncio.wait_for(
                    _run_round_timer(chat_id, engine, message),
                    timeout=engine.round_duration
                )
            except asyncio.TimeoutError:
                # Normal path – round timer expired
                pass

            # ── Mark round inactive IMMEDIATELY ──────────────
            engine.active = False

            # ── Score summary ─────────────────────────────────
            sorted_scores = sorted(
                engine.scores.values(), key=lambda x: x['pts'], reverse=True
            )

            lead_text = "🏆 *ROUND OVER*\n━━━━━━━━━━━━━━━\n"

            if not sorted_scores:
                lead_text += "Nobody scored. Pathetic. Absolutely pathetic."
                engine.empty_rounds += 1
            else:
                engine.empty_rounds = 0

                # Award crates to reaction-claimers
                if engine.crates_dropping > 0 and engine.crate_claimers:
                    for claimer in engine.crate_claimers:
                        add_unclaimed_item(str(claimer['user_id']), "super_crate", 1)
                    lead_text += (
                        f"🎁 *CRATE AWARDS:* "
                        f"{len(engine.crate_claimers)} player(s) claimed crates!\n\n"
                    )

                for i, p in enumerate(sorted_scores):
                    medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i + 1}."
                    lead_text += f"{medal} {p['name']} — {p['pts']} pts\n"

                    # Bonus crates for top 3
                    if i < 3:
                        add_unclaimed_item(p['user_id'], "super_crate", 1)

            lead_text += "\n📊 `!weekly` | `!alltime` for full stats"
            await bot.send_message(chat_id, lead_text, parse_mode="Markdown")

            # ── Level-up celebrations ─────────────────────────
            for user_id, score_data in engine.scores.items():
                if score_data.get("leveled_up"):
                    user = get_user(user_id)
                    if user:
                        current_level = user.get('level', 1)
                        lvl_msg = (
                            f"🎊 *LEVEL UP!* {score_data['name']} reached "
                            f"*LEVEL {current_level}*!\n\n"
                            f"🃏 *GameMaster:* \"Managed not to embarrass yourself. "
                            f"Here's your pathetic reward.\"\n\n"
                            f"✨ Bonus items added! Use `!claims` in DM to collect."
                        )
                        add_unclaimed_item(user_id, "super_crate", 1)
                        if random.random() < 0.5:
                            add_unclaimed_item(user_id, "xp_multiplier", 1, multiplier_value=2)
                        else:
                            add_unclaimed_item(user_id, "silver_multiplier", 1, multiplier_value=2)

                        if current_level % 5 == 0:
                            item_name, item_desc = award_powerful_locked_item(user_id)
                            lvl_msg += (
                                f"\n\n⚡ *MILESTONE!* Unlocked: *{item_name}*\n"
                                f"_{item_desc}_\n"
                                f"⚠️ Too powerful to use. You'll need a bigger backpack not your fanny pack."
                            )
                        await bot.send_message(chat_id, lvl_msg, parse_mode="Markdown")

            # ── Dormancy check ───────────────────────────────
            if engine.empty_rounds >= 3:
                engine.running = False
                engine.active = False
                await bot.send_message(
                    chat_id,
                    "🃏 *GameMaster:* \"Silence. Bore me again and I'll make it permanent. "
                    "Type `!fusion` when you actually want to play.\"",
                    parse_mode="Markdown"
                )
                break

            # ── Help message interval ────────────────────────
            engine.games_played += 1
            if engine.games_played >= engine.games_until_help:
                await bot.send_message(chat_id, get_help_message(), parse_mode="Markdown")
                engine.games_until_help = engine.games_played + random.randint(3, 7)

            # ── Short break between rounds ───────────────────
            await asyncio.sleep(15)

    except asyncio.CancelledError:
        engine.active = False
        engine.running = False


async def _run_round_timer(chat_id: int, engine: GameEngine, message: types.Message):
    """
    Sleeps through the round, sending warnings at key intervals.
    Can be cut short by engine.round_over_event (used by !forcerestart).
    """
    # Crate drop at 50-second mark
    if engine.crates_dropping > 0:
        done = engine.round_over_event.wait()
        try:
            await asyncio.wait_for(asyncio.shield(done), timeout=50)
            return  # round_over_event was set — bail early
        except asyncio.TimeoutError:
            pass

        crate_msg = await bot.send_message(
            chat_id,
            "⚡ *CRATE DROP!* Super Crate!\n"
            "🎁 What will you do now?",
            parse_mode="Markdown"
        )
        engine.crate_drop_message_id = crate_msg.message_id
        engine.crate_claimers = []

        # Wait remaining 70 s (or until force-reset)
        try:
            await asyncio.wait_for(engine.round_over_event.wait(), timeout=70)
            return
        except asyncio.TimeoutError:
            pass
    else:
        # 60-second warning
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

        # Final 60 s
        try:
            await asyncio.wait_for(engine.round_over_event.wait(), timeout=60)
            return
        except asyncio.TimeoutError:
            pass


# ─────────────────────────────────────────────
#  COMMAND HANDLERS
# ─────────────────────────────────────────────

def _sadistic_unreg_reply() -> str:
    """Random snarky message for unregistered users in groups."""
    opts = [
        "🃏 *GameMaster:* \"A ghost? I don't deal with ghosts. "
        "Message me *privately* so I can register your pathetic soul first.\"",

        "🃏 *GameMaster:* \"Who are you? Nobody. "
        "Come to my DMs and prove you exist before thinking of wasting my time.\"",

        "🃏 *GameMaster:* \"I can't see you. "
        "Unregistered souls are invisible to me. "
        "Slide into my DMs. Beg. Beg. Beg. Maybe I'll let you register. Then come back.\"",
    ]
    return random.choice(opts)


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
    engine = get_or_create_engine(chat_id)

    if engine.running:
        await message.answer(
            "🃏 *GameMaster:* \"The souls are already being harvested. Open your eyes.\"",
            parse_mode="Markdown"
        )
        return

    # Warn unregistered caller but still start the game
    u_id = str(message.from_user.id)
    if not get_user(u_id):
        await message.answer(
            "🃏 *GameMaster:* \"You triggered my game without even registering. "
            "Bold. Stupid. Message me privately to join — "
            "but fine, I'll start anyway.\"\n\n"
            "_(Message me in the DM to register your soul!)_",
            parse_mode="Markdown"
        )

    asyncio.create_task(run_auto_harvest(message, chat_id))



@dp.message(F.text == "!forcerestart")
async def force_restart(message: types.Message):
    """Force-end the current round immediately."""
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer(
            "🃏 *GameMaster:* \"Use this in the group, fool.\"",
            parse_mode="Markdown"
        )
        return

    # Only admins can use !forcerestart
    member = await message.chat.get_member(message.from_user.id)
    if member.status not in ["administrator", "creator"]:
        await message.reply(
            "🃏 *GameMaster:* \"You think YOU can restart MY game? Pathetic. Admins only.\"",
            parse_mode="Markdown"
        )
        return

    chat_id = message.chat.id
    engine = get_or_create_engine(chat_id)

    if not engine.running:
        await message.answer(
            "🃏 *GameMaster:* \"Nothing is running. "
            "You can't restart what doesn't exist. Type `!fusion` to start.\"",
            parse_mode="Markdown"
        )
        return

    # Signal the round timer to stop immediately
    engine.round_over_event.set()
    engine.active = False

    await message.answer(
        "🃏 *GameMaster:* \"Fine. FINE. Round terminated. "
        "I'll conjure fresh words momentarily. "
        "This better not be a habit.\"",
        parse_mode="Markdown"
    )


@dp.message_reaction()
async def on_message_reaction(event: types.MessageReactionUpdated):
    """Track reactions to crate drop messages."""
    if not event.user_id:
        return
    chat_id = event.chat.id
    engine = get_or_create_engine(chat_id)

    if (
        engine.crate_drop_message_id == event.message_id
        and engine.crates_dropping > 0
        and event.user_id not in [c['user_id'] for c in engine.crate_claimers]
        and len(engine.crate_claimers) < 3
    ):
        engine.crate_claimers.append({'user_id': event.user_id, 'username': ''})


@dp.message(F.text == "!weekly")
async def show_weekly(message: types.Message):
    u_id = str(message.from_user.id)
    if not get_user(u_id):
        await message.answer(_sadistic_unreg_reply(), parse_mode="Markdown")
        return

    lb = get_weekly_leaderboard()
    if not lb:
        await message.answer(
            "🏆 *WEEKLY LEADERBOARD*\n━━━━━━━━━━━━━━━\nNo one has played yet. Shocking.",
            parse_mode="Markdown"
        )
        return

    text = "🏆 *WEEKLY LEADERBOARD*\n━━━━━━━━━━━━━━━\n"
    for i, p in enumerate(lb, 1):
        medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"{i}."
        text += f"{medal} {p['username']} — {p['points']} pts\n"
    await message.answer(text, parse_mode="Markdown")


@dp.message(F.text == "!alltime")
async def show_alltime(message: types.Message):
    u_id = str(message.from_user.id)
    if not get_user(u_id):
        await message.answer(_sadistic_unreg_reply(), parse_mode="Markdown")
        return

    lb = get_alltime_leaderboard()
    if not lb:
        await message.answer(
            "🏆 *ALL-TIME LEADERBOARD*\n━━━━━━━━━━━━━━━\nBlank. Just like your future.",
            parse_mode="Markdown"
        )
        return

    text = "🏆 *ALL-TIME LEADERBOARD*\n━━━━━━━━━━━━━━━\n"
    for i, p in enumerate(lb, 1):
        medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"{i}."
        text += f"{medal} {p['username']} — {p['points']} pts\n"
    await message.answer(text, parse_mode="Markdown")


# ─────────────────────────────────────────────
#  PROFILE  (DM ONLY)
# ─────────────────────────────────────────────

@dp.message(F.text.startswith("!profile"))
async def show_profile(message: types.Message):
    if message.chat.type != "private":
        await message.answer(
            "🃏 *GameMaster:* \"Oh, trying to broadcast your pitiful stats to the WHOLE group? "
            "How embarrassing. Message me *privately* for that, you narcissist.\"",
            parse_mode="Markdown"
        )
        return

    parts = message.text.split()
    if len(parts) > 1:
        target = parts[1]
        await message.answer(
            f"🃏 *GameMaster:* \"Why are you snooping on *{target}*? "
            "Mind your own business. You can only check YOUR profile here.\"",
            parse_mode="Markdown"
        )
        return

    u_id = str(message.from_user.id)
    profile = get_profile(u_id)
    if not profile:
        await message.answer(
            "🃏 *GameMaster:* \"You have no profile. You don't even exist to me. "
            "Complete the tutorial first.\"",
            parse_mode="Markdown"
        )
        return

    bar_len = 20
    filled = int((profile['xp_progress'] / profile['xp_needed']) * bar_len) if profile['xp_needed'] > 0 else 0
    xp_bar = f"{'█' * filled}{'░' * (bar_len - filled)}"

    text = (
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
        f"└─ Crates: {profile['crate_count']} | Shields: {profile['shield_count']}"
    )
    await message.answer(text, parse_mode="Markdown")


# ─────────────────────────────────────────────
#  INVENTORY  (DM ONLY)
# ─────────────────────────────────────────────

@dp.message(F.text == "!inventory")
async def show_inventory(message: types.Message):
    if message.chat.type != "private":
        await message.answer(
            "🃏 *GameMaster:* \"Exposing your inventory to the whole group? "
            "What kind of fool are you? Message me *privately*, idiot.\"",
            parse_mode="Markdown"
        )
        return

    u_id = str(message.from_user.id)
    if not get_user(u_id):
        await message.answer(
            "🃏 *GameMaster:* \"You have no inventory. You have nothing. "
            "You ARE nothing. Register first.\"",
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
        item_id = item.get("id")
        item_type = item.get("type", "").lower()
        xp_reward = item.get("xp_reward", 0)

        if "wood" in item_type and "crate" in item_type:
            text = f"🪵 WOOD CRATE ({xp_reward} XP)"
            cb = f"open_{item_id}"
        elif "bronze" in item_type and "crate" in item_type:
            text = f"🥉 BRONZE CRATE ({xp_reward} XP)"
            cb = f"open_{item_id}"
        elif "iron" in item_type and "crate" in item_type:
            text = f"⚙️ IRON CRATE ({xp_reward} XP)"
            cb = f"open_{item_id}"
        elif "super" in item_type and "crate" in item_type:
            text = f"🎁 SUPER CRATE ({xp_reward} XP)"
            cb = f"open_{item_id}"
        elif item_type == "shield":
            text = "🛡️ SHIELD [LOCKED]"
            cb = f"use_{item_id}"
        elif item_type == "teleport":
            text = "🌀 TELEPORT (Choose Sector)"
            cb = f"teleport_{item_id}"
        elif "multiplier" in item_type:
            mult = item.get("multiplier_value", 2)
            label = "XP" if "xp" in item_type else "SILVER"
            text = f"⚡ {label} MULTIPLIER x{mult}"
            cb = f"use_{item_id}"
        elif "locked_" in item_type:
            text = f"🔒 LEGENDARY ITEM [TOO POWERFUL]"
            cb = f"info_{item_id}"
        else:
            text = f"❓ {item_type.upper()}"
            cb = f"use_{item_id}"

        keyboard.append([InlineKeyboardButton(text=text, callback_data=cb)])

    profile = get_profile(u_id)
    slots_used = profile['inventory_count'] if profile else len(inventory)
    slots_total = profile['backpack_slots'] if profile else 5

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.answer(
        f"📦 *YOUR INVENTORY*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📊 Slots: {slots_used}/{slots_total}\n"
        f"💡 Tap an item to use it\n\n"
        f"*Your Items:*",
        reply_markup=markup,
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────
#  CLAIMS  (DM ONLY)
# ─────────────────────────────────────────────

@dp.message(F.text == "!claims")
async def show_claims(message: types.Message):
    if message.chat.type != "private":
        await message.answer(
            "🃏 *GameMaster:* \"Exposing your unclaimed loot publicly? "
            "Message me *privately*, you careless fool.\"",
            parse_mode="Markdown"
        )
        return

    u_id = str(message.from_user.id)
    if not get_user(u_id):
        await message.answer(
            "🃏 *GameMaster:* \"No account. No claims. "
            "Register first if you want things from me. Brokie\"",
            parse_mode="Markdown"
        )
        return

    unclaimed = get_unclaimed_items(u_id)
    if not unclaimed:
        await message.answer(
            "🃏 *GameMaster:* \"You have no unclaimed rewards. "
            "Go earn something for once in your life.\"",
            parse_mode="Markdown"
        )
        return

    _item_labels = {
        "xp_multiplier": lambda m: f"⚡ XP MULTIPLIER x{m}",
        "silver_multiplier": lambda m: f"💎 SILVER MULTIPLIER x{m}",
        "super_crate": lambda _: "🎁 SUPER CRATE",
        "wood_crate": lambda _: "🪵 WOOD CRATE",
        "bronze_crate": lambda _: "🥉 BRONZE CRATE",
        "iron_crate": lambda _: "⚙️ IRON CRATE",
        "shield": lambda _: "🛡️ SHIELD",
        "teleport": lambda _: "🌀 TELEPORT",
    }

    keyboard = []
    for item in unclaimed:
        item_type = item.get("type", "").lower()
        mult = item.get("multiplier_value", 0)
        item_id = item.get("id")

        if "locked_" in item_type:
            names = {
                "locked_legendary_artifact": "⚔️ LEGENDARY ARTIFACT",
                "locked_mythical_crown": "👑 MYTHICAL CROWN",
                "locked_void_stone": "🌑 VOID STONE",
                "locked_eternal_flame": "🔥 ETERNAL FLAME",
                "locked_celestial_key": "🗝️ CELESTIAL KEY",
            }
            label = names.get(item_type, "🔒 LEGENDARY ITEM")
            text = f"{label} [TOO POWERFUL — CLAIM ANYWAY]"
        else:
            fn = _item_labels.get(item_type, lambda _: f"🎁 {item_type.upper()}")
            label = fn(mult)
            text = f"{label} [CLAIM]"

        keyboard.append([InlineKeyboardButton(text=text, callback_data=f"claim_{item_id}")])

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.answer(
        f"🎁 *UNCLAIMED REWARDS*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ {len(unclaimed)} item(s) waiting!\n"
        f"💡 Tap *[CLAIM]* to move to inventory\n"
        f"❌ Unclaimed items may expire!",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("claim_"))
async def claim_item_callback(query: types.CallbackQuery):
    u_id = str(query.from_user.id)
    
    # Auto-register if somehow lost
    user = get_user(u_id)
    if not user:
        await query.answer("Your account was lost. Please restart with !tutorial", show_alert=True)
        return
    
    try:
        item_id = int(query.data.split("_")[1])
    except (IndexError, ValueError):
        await query.answer("Invalid item.", show_alert=True)
        return

    success, msg_text = claim_item(u_id, item_id)
    if success:
        await query.answer(f"✅ {msg_text}")
        unclaimed = get_unclaimed_items(u_id)
        if not unclaimed:
            await query.message.edit_text(
                "🃏 *GameMaster:* \"All claimed. Good little minion.\"",
                parse_mode="Markdown"
            )
        else:
            await query.message.edit_text(
                f"🎁 *UNCLAIMED REWARDS*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Remaining: *{len(unclaimed)}* item(s)\n"
                f"Send `!claims` again to see them.",
                parse_mode="Markdown"
            )
    else:
        await query.answer(f"❌ {msg_text}", show_alert=True)


# ─────────────────────────────────────────────
#  CRATE & ITEM CALLBACKS
# ─────────────────────────────────────────────

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
    user = get_user(user_id)
    if not user:
        await message.answer(
            "🃏 *GameMaster:* \"You don't exist to me. Register first.\"",
            parse_mode="Markdown"
        )
        return

    inventory = get_inventory(user_id)
    crate = next((it for it in inventory if it.get("id") == item_id), None)
    if not crate:
        await message.answer("🃏 *GameMaster:* \"Invalid crate.\"", parse_mode="Markdown")
        return

    if "crate" not in crate.get("type", "").lower():
        await message.answer(
            "🃏 *GameMaster:* \"That's not a crate. Learn to read or don't, who cares?.\"",
            parse_mode="Markdown"
        )
        return

    xp_reward = crate.get("xp_reward", 0)
    crate_type = crate.get("type", "unknown").replace("_", " ").upper()

    add_xp(user_id, xp_reward)
    remove_inventory_item(user_id, crate.get("id"))

    await message.answer(
        f"✨ *CRATE OPENED!*\n📦 {crate_type}\n+{xp_reward} XP",
        parse_mode="Markdown"
    )


async def _use_item(message: types.Message, user_id: str, item_id: int):
    user = get_user(user_id)
    if not user:
        await message.answer(
            "🃏 *GameMaster:* \"You don't exist to me. Register first.\"",
            parse_mode="Markdown"
        )
        return

    inventory = get_inventory(user_id)
    item = next((it for it in inventory if it.get("id") == item_id), None)
    if not item:
        await message.answer("🃏 *GameMaster:* \"Invalid item.\"", parse_mode="Markdown")
        return

    item_type = item.get("type", "unknown").lower()

    if "crate" in item_type:
        await message.answer(
            "🃏 *GameMaster:* \"That's a crate. Use the OPEN button, you simpleton.\"",
            parse_mode="Markdown"
        )
        return
    if item_type == "shield":
        await message.answer(
            "🃏 *GameMaster:* \"Shield mechanics are still being forged. "
            "Sit tight, impatient one. Soon you'll have the power to protect yourself. For now enjoy your temporary protection, maggot.\"",
            parse_mode="Markdown"
        )
        return
    if "locked_" in item_type:
        await message.answer(
            "🃏 *GameMaster:* \"You can't USE that. It would destroy your soul. "
            "And possibly me. Upgrade your backpack first.\"",
            parse_mode="Markdown"
        )
        return

    await message.answer(
        "🃏 *GameMaster:* \"Unknown item. Even I don't know what this is.\"",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────
#  TELEPORT CALLBACKS
# ─────────────────────────────────────────────

@dp.callback_query(F.data.startswith("teleport_to_"))
async def teleport_destination(callback: types.CallbackQuery):
    import re
    m = re.search(r'\d+', callback.data)
    if not m:
        return

    sector_id = int(m.group())
    u_id = str(callback.from_user.id)
    
    user = get_user(u_id)
    if not user:
        await callback.answer("Your account was lost. Restart with !tutorial", show_alert=True)
        return

    if sector_id < 1 or sector_id > 9:
        await callback.answer("That sector is locked! Not opened yet to scallywags, losers and anyone in general.", show_alert=True)
        return

    all_sectors = load_sectors()
    info = all_sectors.get(sector_id, {})
    sector_name = info.get("name", f"Sector {sector_id}") if info else f"Sector {sector_id}"
    sector_env = info.get("environment", "") if info else ""
    sector_perks = info.get("perks", "") if info else ""
    set_sector(u_id, sector_id)

    inventory = get_inventory(u_id)
    for item in inventory:
        if item.get("type", "").lower() == "teleport":
            remove_inventory_item(u_id, item.get("id"))
            break

    await callback.answer("✨ Teleported!")
    await callback.message.edit_text(
        f"✨ *TELEPORTATION COMPLETE*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📍 Arrived at: *#{sector_id} {sector_name.upper()}*\n"
        + (f"🌍 {sector_env}\n" if sector_env else "")
        + (f"⚡ Perks: {sector_perks}\n" if sector_perks else "")
        + f"\nYour teleport has been consumed. Explore the sector and earn new rewards!",
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("teleport_"))
async def teleport_item_callback(callback: types.CallbackQuery):
    import re
    m = re.search(r'\d+', callback.data)
    if not m:
        return

    item_id = int(m.group())
    u_id = str(callback.from_user.id)
    
    user = get_user(u_id)
    if not user:
        await callback.answer("Your account was lost. Restart with !tutorial", show_alert=True)
        return
    
    inventory = get_inventory(u_id)

    item = next((it for it in inventory if it.get("id") == item_id), None)
    if not item:
        await callback.answer("Invalid item. what are you trying to do? Lose your mind?", show_alert=True)
        return

    if item.get("type", "").lower() != "teleport":
        await callback.answer("That's not a teleport! Oh, so you can't see now to where you're going? Pitiful", show_alert=True)
        return

    await callback.answer("Choose your destination!")
    all_sectors = load_sectors()
    keyboard = []
    for sid in range(1, 10):
        info = all_sectors.get(sid, {})
        sname = info.get("name", f"Sector {sid}") if info else f"Sector {sid}"
        perks = info.get("perks", "") if info else ""
        btn_text = f"#{sid} {sname}  {perks}" if perks else f"#{sid} {sname}"
        keyboard.append([InlineKeyboardButton(
            text=btn_text,
            callback_data=f"teleport_to_{sid}"
        )])
    keyboard.append([InlineKeyboardButton(
        text="🔒 Sectors 10-64 (LOCKED — Level up to unlock)",
        callback_data="locked_sectors"
    )])

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback.message.answer(
        "🌀 *TELEPORT NETWORK*\n"
        "━━━━━━━━━━━━━━━\n"
        "Available destinations:\n\n"
        "_Choose your sector:_",
        parse_mode="Markdown",
        reply_markup=markup
    )


@dp.callback_query(F.data == "locked_sectors")
async def locked_sectors_info(callback: types.CallbackQuery):
    await callback.answer(
        "Sectors 10-64 unlock as you level up!",
        show_alert=True
    )


# ─────────────────────────────────────────────
#  MISC COMMANDS
# ─────────────────────────────────────────────

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
        "For now, manage your measly 5 slots and stop complaining with what you've got.\"",
        parse_mode="Markdown"
    )


@dp.message(F.text.startswith("!changename"))
async def change_name(message: types.Message):
    if message.chat.type != "private":
        await message.answer(
            "🃏 *GameMaster:* \"Handle your identity crises in *private*, not here.\"",
            parse_mode="Markdown"
        )
        return

    u_id = str(message.from_user.id)
    user = get_user(u_id)
    if not user:
        await message.answer(
            "🃏 *GameMaster:* \"You're not registered. "
            "You can't change a name you don't have. Nomad.\"",
            parse_mode="Markdown"
        )
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "🃏 *GameMaster:* \"Usage: `!changename NewName`\"",
            parse_mode="Markdown"
        )
        return

    new_name = parts[1].strip()[:20]
    old_name = user.get('username', message.from_user.first_name)

    if new_name.lower() == old_name.lower():
        await message.answer(
            f"🃏 *GameMaster:* \"You're already '{old_name}'. "
            "Did you hit your head? Changing nothing. As usual.\"",
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
        await message.answer(
            "🃏 *GameMaster:* \"Handle the tutorial in *private*. Go.\"",
            parse_mode="Markdown"
        )
        return

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from initiation import Trial

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ I'm ready", callback_data="trial_yes")],
        [InlineKeyboardButton(text="🚪 Never mind", callback_data="trial_no")]
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
        await message.answer(
            "🃏 *GameMaster:* \"Message me privately, fool.\"",
            parse_mode="Markdown"
        )
        return

    u_id = str(message.from_user.id)
    if get_user(u_id):
        await message.answer(
            "🃏 *GameMaster:* \"You're already registered. Stop pestering me.\"",
            parse_mode="Markdown"
        )
        return

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from initiation import Trial

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ I'm ready to enter", callback_data="trial_yes")],
        [InlineKeyboardButton(text="🚪 I'm just lost", callback_data="trial_no")]
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


# ─────────────────────────────────────────────
#  CRATE OPEN COMMAND
# ─────────────────────────────────────────────

@dp.message(F.text.regexp(r"^!open\s+\d+$"))
async def crate_open_handler(message: types.Message):
    if message.chat.type != "private":
        await message.answer(
            "🃏 *GameMaster:* \"Open crates in *private*, not here.\"",
            parse_mode="Markdown"
        )
        return
    import re
    m = re.search(r'\d+', message.text)
    if m:
        pos = int(m.group()) - 1  # 1-based → 0-based
        inventory = get_inventory(str(message.from_user.id))
        if 0 <= pos < len(inventory):
            await _open_crate(message, str(message.from_user.id), inventory[pos]["id"])


@dp.message(F.text.regexp(r"^!use\s+\d+$"))
async def use_item_handler(message: types.Message):
    if message.chat.type != "private":
        await message.answer(
            "🃏 *GameMaster:* \"Use items in *private*, fool.\"",
            parse_mode="Markdown"
        )
        return
    import re
    m = re.search(r'\d+', message.text)
    if m:
        pos = int(m.group()) - 1  # 1-based → 0-based
        inventory = get_inventory(str(message.from_user.id))
        if 0 <= pos < len(inventory):
            await _use_item(message, str(message.from_user.id), inventory[pos]["id"])


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def on_group_message(message: types.Message):
    """Process word guesses from group chat."""
    if not message.text:
        return

    text = message.text.strip()

    chat_id = message.chat.id
    engine = get_or_create_engine(chat_id)
    u_id = str(message.from_user.id)

    # ── New player enters the group ───────────────────────
    user = get_user(u_id)
    if not user:
        # Only react occasionally so it's not spammy for every message
        if random.random() < 0.3:
            await message.reply(
                "🃏 *GameMaster:* \"Who dares speak in my arena unregistered?\"\n\n"
                "\"Your soul is not in my records. "
                "Come to my DMs first, register, *then* you may waste your time. Definitely not mine.\"",
                parse_mode="Markdown"
            )
        return

    # ── Word repeat every 4 messages ─────────────────────
    if engine.active:
        engine.message_count += 1
        if engine.message_count >= 4:
            await message.answer(
                f"📌 *The words are still:* `{engine.word1}`  `{engine.word2}`",
                parse_mode="Markdown"
            )
            engine.message_count = 0

    # ── Must be in active round ───────────────────────────
    if not engine.active:
        guess = text.lower()
        if len(guess) >= 3 and engine.letters and is_anagram(guess, engine.letters):
            await message.reply(
                "🛑 *GameMaster:* \"The round is OVER. "
                "Are you slow? Type `!fusion` to start a new one.\"",
                parse_mode="Markdown"
            )
        return

    guess = text.lower()

    # Minimum length
    if len(guess) < 3:
        return

    # Already used
    if guess in engine.used_words:
        await message.reply(f"❌ `{guess.upper()}` was already guessed this round!")
        return

    # Anagram check
    if not is_anagram(guess, engine.letters):
        return  # silently ignore non-anagrams

    # Dictionary check
    if await check_supabase_dict(guess):
        pts = len(guess) - 2
        engine.used_words.append(guess)

        # Load user to get database username
        user = get_user(u_id)
        username = user.get("username", message.from_user.first_name) if user else message.from_user.first_name
        
        add_points(u_id, pts, username)
        add_xp(u_id, pts)
        old_level, new_level = check_level_up(u_id)

        if u_id not in engine.scores:
            engine.scores[u_id] = {
                "pts": 0,
                "name": username,
                "user_id": u_id,
                "leveled_up": False
            }
        engine.scores[u_id]["pts"] += pts

        feedback = f"✅ `{guess.upper()}` +{pts} pts  ⭐ +{pts} XP"
        if old_level and new_level:
            feedback += f"\n🎊 *LEVEL UP!* {old_level} → {new_level}"
            engine.scores[u_id]["leveled_up"] = True

        await message.reply(feedback, parse_mode="Markdown")


# ─────────────────────────────────────────────
#  LEADERBOARDS
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
