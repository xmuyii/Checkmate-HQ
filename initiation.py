import asyncio
import random
import httpx
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import (
    get_user, register_user, add_silver, set_sector,
    add_inventory_item, get_profile, add_xp, save_user, load_sectors,
    add_unclaimed_item, get_sector_display, get_unclaimed_items
)

# ── Config (Read from Environment Variables) ──────────────────────────────────
API_TOKEN    = os.environ.get('API_TOKEN', '8770224655:AAHwmeIEnu0IPusu8XzumEhmMOLGcCGqMs0')
SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://basniiolppmtpzishhtn.supabase.co').rstrip('/')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJhc25paW9scHBtdHB6aXNoaHRuIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTQ3NjMwOCwiZXhwIjoyMDkxMDUyMzA4fQ.qrj1BO5dNilRDvgKtvTdwIWjBhFTRyGzuHPD271Xcac')
CHECKMATE_HQ_GROUP_ID = int(os.environ.get('CHECKMATE_HQ_GROUP_ID', '-1003835925366'))

bot = Bot(token=API_TOKEN)
initiation_router = Router()

# Track premium timers (not wired to payment yet)
premium_timers: dict = {}


# ── FSM states ────────────────────────────────────────────────────────────

class Trial(StatesGroup):
    awaiting_username = State()
    trial_round_1     = State()
    trial_round_2     = State()
    trial_round_3     = State()
    backpack_choice   = State()


# ── Helpers ───────────────────────────────────────────────────────────────

async def fetch_trial_words():
    headers = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
    url = f"{SUPABASE_URL}/rest/v1/Dictionary?word_length=eq.6&select=word&limit=1"
    async with httpx.AsyncClient() as client:
        try:
            r1 = await client.get(f"{url}&offset={random.randint(0, 500)}", headers=headers)
            r2 = await client.get(f"{url}&offset={random.randint(0, 500)}", headers=headers)
            w1 = r1.json()[0]['word'].upper() if r1.json() else "PYTHON"
            w2 = r2.json()[0]['word'].upper() if r2.json() else "PLAYER"
            return w1, w2
        except Exception:
            return "PYTHON", "PLAYER"

def is_anagram(guess: str, letters_pool: str) -> bool:
    pool = list(letters_pool)
    for ch in guess:
        if ch in pool:
            pool.remove(ch)
        else:
            return False
    return True

async def check_dict(word: str) -> bool:
    headers = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/Dictionary?word=eq.{word}&select=word",
                headers=headers, timeout=5.0
            )
            return len(r.json()) > 0
        except Exception:
            return False


# ── FIRST CONTACT ─────────────────────────────────────────────────────────

@initiation_router.message(StateFilter(None), F.chat.type == "private")
async def first_contact(message: types.Message, state: FSMContext):
    """Called for any private message when no FSM state is active."""
    user_id = str(message.from_user.id)

    # Skip commands — they have their own handlers in main.py
    if message.text and message.text.startswith("!"):
        return

    # Already registered: just greet them
    if get_user(user_id):
        await message.answer(
            "🃏 *GameMaster:* \"Oh, it's you again. Your soul is already in my ledger. "
            "Go play — type `!fusion` in the group.\"\n\n"
            "Need help? Type `!help`.",
            parse_mode="Markdown"
        )
        return

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ I'm ready to enter", callback_data="trial_yes")],
        [InlineKeyboardButton(text="🚪 I'm just lost",       callback_data="trial_no")],
    ])
    await message.answer(
        "🃏 *GameMaster:* \"Well, well, well. Look what crawled into my domain.\"\n\n"
        "\"You show up unannounced, uninvited, and probably unprepared. "
        "I don't know who you are. I don't care who you are.\"\n\n"
        "\"But something about you... *interests* me. Maybe it's the desperation. "
        "Or maybe you're just incredibly stupid.\"\n\n"
        "\"So tell me, little mortal: are you here to join *The 64*? "
        "Or did you just wander in by accident?\"",
        parse_mode="Markdown",
        reply_markup=markup
    )
    await state.set_state(Trial.awaiting_username)


# ── Entry decision buttons ────────────────────────────────────────────────

@initiation_router.callback_query(Trial.awaiting_username, F.data == "trial_no")
async def decline_entry(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "🃏 *GameMaster:* \"Thought so. Weak. *Pathetic.*\"\n\n"
        "\"Come back if you ever grow a spine.\"",
        parse_mode="Markdown"
    )
    await state.clear()


@initiation_router.callback_query(Trial.awaiting_username, F.data == "trial_yes")
async def accept_entry(callback: types.CallbackQuery, state: FSMContext):
    """Ask for a username — but skip this step if already registered."""
    await callback.answer()
    user_id = str(callback.from_user.id)
    user    = get_user(user_id)

    if user:
        # Already registered — jump straight to trials without asking for name
        username = user.get("username", callback.from_user.first_name)
        await state.update_data(username=username, scores_list=[])
        await callback.message.edit_text(
            f"🃏 *GameMaster:* \"Oh, *{username}*. You again. "
            "Fine — let's see if you've improved since last time.\"\n\n"
            "\"Three trials. Same rules. Don't embarrass yourself.\"",
            parse_mode="Markdown"
        )
        await asyncio.sleep(1)
        await send_trial_letters(callback.message, state, 0)
    else:
        await callback.message.edit_text(
            "🃏 *GameMaster:* \"Good. Foolish, but good.\"\n\n"
            "\"I need your name for my records. What shall I call you?\"",
            parse_mode="Markdown"
        )
        # State stays as Trial.awaiting_username so capture_username fires next


# ── Username capture (new players only) ──────────────────────────────────

@initiation_router.message(Trial.awaiting_username)
async def capture_username(message: types.Message, state: FSMContext):
    username = message.text.strip()[:20]
    user_id  = str(message.from_user.id)

    # register_user is now safe — it won't overwrite existing accounts
    register_user(user_id, username)

    await state.update_data(username=username, scores_list=[])
    await message.answer(
        f"🃏 *GameMaster:* \"{username}. *Derivative.*\"\n\n"
        "\"You must survive three trials. Prove you have even a shred of wit.\"\n\n"
        "\"Let's begin.\"",
        parse_mode="Markdown"
    )
    await asyncio.sleep(1)
    await send_trial_letters(message, state, 0)


# ── Trial letter rounds ───────────────────────────────────────────────────

async def send_trial_letters(message: types.Message, state: FSMContext, round_num: int):
    if round_num > 2:
        return

    word1, word2 = await fetch_trial_words()
    letters      = (word1 + word2).lower()

    await state.update_data(
        trial_letters=letters, trial_word1=word1, trial_word2=word2,
        trial_round=round_num, trial_round_score=0, trial_used=[]
    )

    names  = ["FIRST", "SECOND", "FINAL"]
    states = [Trial.trial_round_1, Trial.trial_round_2, Trial.trial_round_3]

    await message.answer(
        f"💎 *{names[round_num]} TRIAL*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📝 *LETTERS:* `{' '.join(letters.upper())}`\n\n"
        f"Form as many words as you can from these letters.\n"
        f"Type `!done` when you're finished.",
        parse_mode="Markdown"
    )
    await state.set_state(states[round_num])


# ── Trial guess handlers ──────────────────────────────────────────────────

@initiation_router.message(Trial.trial_round_1)
@initiation_router.message(Trial.trial_round_2)
@initiation_router.message(Trial.trial_round_3)
async def on_trial_guess(message: types.Message, state: FSMContext):
    data  = await state.get_data()
    guess = message.text.lower().strip() if message.text else ""

    if guess == "!done":
        await end_trial_round(message, state)
        return

    letters = data.get('trial_letters', '')
    used    = data.get('trial_used', [])

    if is_anagram(guess, letters) and guess not in used:
        if await check_dict(guess):
            pts   = max(len(guess) - 2, 1)
            score = data.get('trial_round_score', 0) + pts
            used.append(guess)
            await state.update_data(trial_round_score=score, trial_used=used)
            await message.reply(f"✅ `{guess.upper()}` +{pts} pts", parse_mode="Markdown")


# ── End of each trial round ───────────────────────────────────────────────

async def end_trial_round(message: types.Message, state: FSMContext):
    data        = await state.get_data()
    round_num   = data.get('trial_round', 0)
    score       = data.get('trial_round_score', 0)
    username    = data.get('username', message.from_user.first_name)
    scores_list = data.get('scores_list', [])
    scores_list.append(score)

    if round_num == 2:
        # Final round — player has highest score but placed last (lore / tutorial flavour)
        lead_text = (
            f"🏆 *FINAL TRIAL LEADERBOARD*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🥇 Predator\\_99 — 45 pts\n"
            f"🥈 ShadowMaster — 38 pts\n"
            f"🥉 Vortex\\_7 — 31 pts\n"
            f"4\\. Knight\\_44 — 28 pts\n"
            f"5\\. Breaker\\_12 — 25 pts\n"
            f"6\\. Phoenix\\_8 — 22 pts\n"
            f"7\\. Rogue\\_33 — 18 pts\n"
            f"8\\. Storm\\_5 — 15 pts\n"
            f"9\\. Sentinel\\_2 — 12 pts\n"
            f"10\\. *{username} — {score} pts*\n\n"
            f"🃏 *GameMaster:* \"HIGHEST SCORE\\. LOWEST RANK\\. "
            f"How *absurdly pathetic*\\! But I admire the cosmic joke\\. "
            f"Take 100 silver\\.\""
        )
        await message.answer(lead_text, parse_mode="MarkdownV2")
        await asyncio.sleep(2)
        add_silver(str(message.from_user.id), 100, username)
        await state.update_data(scores_list=scores_list)
        await show_backpack_choice(message, state)
    else:
        placement_text = (
            f"🏆 *ROUND {round_num + 1} LEADERBOARD*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🥇 ShadowMaster — {score + 10} pts\n"
            f"🥈 Predator\\_99 — {score + 5} pts\n"
            f"🥉 *{username} — {score} pts*\n\n"
            f"🃏 *GameMaster:* \"Next\\.\""
        )
        await message.answer(placement_text, parse_mode="MarkdownV2")
        await asyncio.sleep(2)
        await state.update_data(scores_list=scores_list)
        await send_trial_letters(message, state, round_num + 1)


# ── Backpack choice ───────────────────────────────────────────────────────

async def show_backpack_choice(message: types.Message, state: FSMContext):
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👜 Queen's Satchel (900 ₦) [LOCKED]", callback_data="backpack_premium")],
        [InlineKeyboardButton(text="🎒 Normal Backpack (FREE)",            callback_data="backpack_default")],
    ])
    await message.answer(
        "💰 *100 SILVER AWARDED*\n\n"
        "🎒 *Choose your vessel:*\n"
        "• Queen's Satchel: 20 inventory slots (900 ₦) — *payment system coming soon*\n"
        "• Normal Backpack: 5 inventory slots (FREE)",
        parse_mode="Markdown",
        reply_markup=markup
    )
    await state.set_state(Trial.backpack_choice)


@initiation_router.callback_query(Trial.backpack_choice)
async def backpack_choice_handler(callback: types.CallbackQuery, state: FSMContext):
    data     = await state.get_data()
    user_id  = str(callback.from_user.id)
    username = data.get('username', callback.from_user.first_name)
    choice   = callback.data

    if choice == "backpack_premium":
        await callback.answer(
            "Payment system coming soon! Please choose the Normal Backpack for now.",
            show_alert=True
        )
        return  # keep keyboard visible

    await callback.answer("✅ Backpack equipped!")

    user = get_user(user_id)

    # ── Repeat tutorial: no rewards, just remind them where they are ──────
    if user and user.get("completed_tutorial"):
        sector_id = user.get("sector")
        all_sectors = load_sectors()
        info = all_sectors.get(int(sector_id), {}) if sector_id else {}
        sector_name = info.get("name", f"Sector {sector_id}") if info else f"Sector {sector_id}"

        await callback.message.edit_text(
            "🃏 *GameMaster:* \"Trying to farm rewards again? *Adorably* transparent.\"\n\n"
            "\"I've already seen all your tricks. You get NOTHING this time.\"\n\n"
            f"📍 You're already in: *#{sector_id} {sector_name.upper()}*\n"
            "🎒 Backpack: Normal (5 slots)\n\n"
            "Now go play. Type `!fusion` in the group.",
            parse_mode="Markdown"
        )
        await state.clear()
        return

    # ── First-time completion ─────────────────────────────────────────────
    all_sectors = load_sectors()

    if user and user.get("sector"):
        sector_id = int(user["sector"])
        info      = all_sectors.get(sector_id, {})
    else:
        sector_id = random.randint(1, 9)
        info      = all_sectors.get(sector_id, {})

    sector_name = info.get("name", f"Sector {sector_id}") if info else f"Sector {sector_id}"
    sector_env  = info.get("environment", "") if info else ""
    sector_perks = info.get("perks", "") if info else ""

    set_sector(user_id, sector_id)

    # Persist tutorial completion
    if user:
        user["backpack_image"]     = "normal_backpack"
        user["backpack_slots"]     = 5
        user["completed_tutorial"] = True
        save_user(user_id, user)

    # Award starter items as unclaimed
    add_unclaimed_item(user_id, "shield", 1)
    for crate_type, xp in [
        ("wood_crate",   random.randint(50, 100)),
        ("bronze_crate", random.randint(100, 150)),
        ("iron_crate",   random.randint(150, 200)),
    ]:
        add_unclaimed_item(user_id, crate_type, amount=1, xp_reward=xp)
    add_unclaimed_item(user_id, "teleport", 1)

    item_count = len(get_unclaimed_items(user_id))

    env_line   = f"🗺️ {sector_env}\n"   if sector_env   else ""
    perks_line = f"⚡ Perks: {sector_perks}\n" if sector_perks else ""

    await callback.message.edit_text(
        f"✨ *TUTORIAL COMPLETE!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌍 *YOU ARE BEING DROPPED IN:*\n"
        f"📍 *#{sector_id} {sector_name.upper()}*\n"
        f"{env_line}{perks_line}\n"
        f"🎒 *BACKPACK:* Normal Backpack (5 slots)\n\n"
        f"🎁 *STARTER REWARDS WAITING* — {item_count} items!\n\n"
        f"📦 Items Awaiting:\n"
        f"🛡️ Shield\n"
        f"🪵 Wood Crate\n"
        f"🥉 Bronze Crate\n"
        f"⚙️ Iron Crate\n"
        f"🌀 Teleport (sectors 1-9)\n\n"
        f"*WHAT TO DO NEXT:*\n"
        f"1️⃣ DM me `!claims` — see your unclaimed items\n"
        f"2️⃣ Tap [CLAIM] on each item to add it to your inventory\n"
        f"3️⃣ DM me `!inventory` — see your claimed items\n"
        f"4️⃣ Go to *Checkmate HQ* group\n"
        f"5️⃣ Type `!fusion` to start playing!\n\n"
        f"🃏 *GameMaster:* \"Welcome to The 64, {username}. "
        f"Try not to disappoint me.\"",
        parse_mode="Markdown"
    )
    await state.clear()
