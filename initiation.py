import asyncio
import random
import httpx
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatMember
from database import (
    get_user, register_user, add_silver, set_sector, 
    add_inventory_item, get_profile, add_xp, save_user, load_sectors,
    add_unclaimed_item, get_sector_display
)

# Reuse credentials from main.py
API_TOKEN = '8770224655:AAFAHmPzdwCRbi5Y7VfO6cCBpuHZdT_dI2I'
SUPABASE_URL = 'https://basniiolppmtpzishhtn.supabase.co'.rstrip('/')
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJhc25paW9scHBtdHB6aXNoaHRuIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTQ3NjMwOCwiZXhwIjoyMDkxMDUyMzA4fQ.qrj1BO5dNilRDvgKtvTdwIWjBhFTRyGzuHPD271Xcac'

# GROUP CONFIG
CHECKMATE_HQ_GROUP_ID = -1003835925366  # Replace with actual Checkmate HQ group ID

bot = Bot(token=API_TOKEN)

# Router for initiation handlers
initiation_router = Router()

# Track premium upgrade expirations (user_id -> expiration_time)
premium_timers = {}

# --- TRIAL FSM STATES ---
class Trial(StatesGroup):
    awaiting_username = State()
    trial_round_1 = State()
    trial_round_2 = State()
    trial_round_3 = State()
    backpack_choice = State()


# --- HELPER FUNCTIONS ---

async def fetch_trial_words():
    """Fetch two random 6-7 letter words for trials"""
    headers = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
    url = f"{SUPABASE_URL}/rest/v1/Dictionary?word_length=eq.6&select=word&limit=1"
    async with httpx.AsyncClient() as client:
        try:
            r1 = await client.get(f"{url}&offset={random.randint(0, 500)}", headers=headers)
            r2 = await client.get(f"{url}&offset={random.randint(0, 500)}", headers=headers)
            w1 = r1.json()[0]['word'].upper() if r1.json() else "PYTHON"
            w2 = r2.json()[0]['word'].upper() if r2.json() else "PLAYER"
            return w1, w2
        except:
            return "PYTHON", "PLAYER"

def is_anagram(guess, letters_pool):
    """Check if word is anagram of letters"""
    pool = list(letters_pool)
    for char in guess:
        if char in pool:
            pool.remove(char)
        else:
            return False
    return True

async def check_dict(word):
    """Check if word exists in dictionary"""
    headers = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SUPABASE_URL}/rest/v1/Dictionary?word=eq.{word}&select=word", headers=headers)
        return len(r.json()) > 0


# --- 🎬 FIRST CONTACT: THE SADISTIC WELCOME ---

@initiation_router.message(StateFilter(None), F.chat.type == "private")
async def first_contact(message: types.Message, state: FSMContext):
    """Sadistic welcome for anyone messaging the bot privately"""
    user_id = str(message.from_user.id)
    
    # If already registered, don't bother
    if get_user(user_id):
        await message.answer(
            "🃏 *GameMaster:* \"You're already here. Stop pestering me.\"",
            parse_mode="Markdown"
        )
        return
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ I'm ready to enter", callback_data="trial_yes")],
        [InlineKeyboardButton(text="🚪 I'm just lost", callback_data="trial_no")]
    ])
    
    await message.answer(
        "🃏 *GameMaster:* \"Well, well, well. Look what crawled into my domain.\"\n\n"
        "\"You show up unannounced, uninvited, and probably unprepared. I don't know who you are. "
        "I don't care who you are.\"\n\n"
        "\"But something about you... *interests* me. Maybe it's the desperation in your message. "
        "Or maybe you're just incredibly stupid.\"\n\n"
        "\"So tell me, little mortal: are you here to join *The 64*? Or did you just wander in by accident?\"",
        parse_mode="Markdown",
        reply_markup=markup
    )
    await state.set_state(Trial.awaiting_username)


@initiation_router.callback_query(Trial.awaiting_username, F.data == "trial_no")
async def decline_entry(callback: types.CallbackQuery, state: FSMContext):
    """User declines to enter"""
    await callback.answer()
    await callback.message.edit_text(
        "🃏 *GameMaster:* \"Thought so. Weak. *Pathetic.*\"\n\n"
        "\"Come back if you ever grow a spine.\"",
        parse_mode="Markdown"
    )
    await state.clear()


@initiation_router.callback_query(Trial.awaiting_username, F.data == "trial_yes")
async def accept_entry(callback: types.CallbackQuery, state: FSMContext):
    """User accepts entry - ask for username"""
    await callback.answer()
    await callback.message.edit_text(
        "🃏 *GameMaster:* \"Good. Foolish, but good.\"\n\n"
        "\"I need your name for my records. What shall I call you?\"",
        parse_mode="Markdown"
    )


# --- USERNAME CAPTURE ---

@initiation_router.message(Trial.awaiting_username)
async def capture_username(message: types.Message, state: FSMContext):
    """Capture player username and start first trial"""
    username = message.text.strip()[:20]
    user_id = str(message.from_user.id)
    
    # Register player
    register_user(user_id, username)
    
    await state.update_data(username=username, scores_list=[])
    
    await message.answer(
        f"🃏 *GameMaster:* \"{username}. *Derivative.*\"\n\n"
        f"\"You must survive three trials. Prove you have even a shred of wit.\"\n\n"
        f"\"Let's begin.\"",
        parse_mode="Markdown"
    )
    
    await asyncio.sleep(1)
    await send_trial_letters(message, state, 0)


async def send_trial_letters(message: types.Message, state: FSMContext, round_num: int):
    """Send trial round letters"""
    if round_num > 2:
        return
    
    word1, word2 = await fetch_trial_words()
    letters = (word1 + word2).lower()
    
    await state.update_data(
        trial_letters=letters,
        trial_word1=word1,
        trial_word2=word2,
        trial_round=round_num,
        trial_round_score=0,
        trial_used=[]
    )
    
    names = ["FIRST", "SECOND", "FINAL"]
    states = [Trial.trial_round_1, Trial.trial_round_2, Trial.trial_round_3]
    
    await message.answer(
        f"💎 *{names[round_num]} TRIAL*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📝 **LETTERS:** {' '.join(letters.upper())}\n\n"
        f"Find all the words you can form from these letters. Type `!done` when ready to see the results.",
        parse_mode="Markdown"
    )
    
    await state.set_state(states[round_num])


# --- TRIAL RESPONSES ---

@initiation_router.message(Trial.trial_round_1)
@initiation_router.message(Trial.trial_round_2)
@initiation_router.message(Trial.trial_round_3)
async def on_trial_guess(message: types.Message, state: FSMContext):
    """Process trial guesses"""
    data = await state.get_data()
    guess = message.text.lower().strip()
    
    if guess == "!done":
        await end_trial_round(message, state)
        return
    
    round_num = data['trial_round']
    letters = data['trial_letters']
    used = data.get('trial_used', [])
    
    # Validate
    if is_anagram(guess, letters) and guess not in used:
        if await check_dict(guess):
            pts = len(guess) - 2
            used.append(guess)
            score = data.get('trial_round_score', 0) + pts
            
            await state.update_data(trial_round_score=score, trial_used=used)
    elif guess in used:
        pass  # Silently ignore duplicates


async def end_trial_round(message: types.Message, state: FSMContext):
    """End current round and show rigged leaderboard"""
    data = await state.get_data()
    round_num = data['trial_round']
    score = data.get('trial_round_score', 0)
    username = data['username']
    scores_list = data.get('scores_list', [])
    scores_list.append(score)
    
    if round_num == 2:
        # FINAL ROUND - They have highest score but placed LAST
        lead_text = f"""🏆 *FINAL TRIAL LEADERBOARD*
━━━━━━━━━━━━━━━
🥇 Predator\_99 — 45 pts
🥈 ShadowMaster — 38 pts
🥉 Vortex\_7 — 31 pts
4. Knight\_44 — 28 pts
5. Breaker\_12 — 25 pts
6. Phoenix\_8 — 22 pts
7. Rogue\_33 — 18 pts
8. Storm\_5 — 15 pts
9. Sentinel\_2 — 12 pts
10. **{username} — {score} pts**

🃏 *GameMaster:* \"HIGHEST SCORE. LOWEST RANK. How *absurdly pathetic*! But I admire the cosmic joke. Take 100 silver.\" """
        
        await message.answer(lead_text, parse_mode="Markdown")
        
        await asyncio.sleep(2)
        
        # Award silver
        add_silver(str(message.from_user.id), 100, username)
        await state.update_data(scores_list=scores_list)
        await show_backpack_choice(message, state, data)
    else:
        # Rounds 1-2: Normal rigged placements
        placement_text = f"""🏆 *ROUND {round_num + 1} LEADERBOARD*
━━━━━━━━━━━━━━━
🥇 ShadowMaster — {score + 10} pts
🥈 Predator\_99 — {score + 5} pts
🥉 **{username} — {score} pts**"""
        
        placement_text += f"\n\n🃏 *GameMaster:* \"Next.\""
        
        await message.answer(placement_text, parse_mode="Markdown")
        
        await asyncio.sleep(2)
        await state.update_data(scores_list=scores_list)
        await send_trial_letters(message, state, round_num + 1)


async def show_backpack_choice(message: types.Message, state: FSMContext, data: dict):
    """Show backpack upgrade options"""
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👜 Queen's Satchel (900 ₦) [LOCKED]", callback_data="backpack_premium")],
        [InlineKeyboardButton(text="🎒 Normal Backpack (FREE)", callback_data="backpack_default")]
    ])
    
    await message.answer(
        f"💰 **100 SILVER AWARDED**\n\n"
        f"🎒 *Choose your vessel:*\n"
        f"• The Queen's Satchel: 20 inventory slots (900 ₦) - *Payment system locked*\n"
        f"• Normal Backpack: 5 inventory slots (FREE)",
        parse_mode="Markdown",
        reply_markup=markup
    )
    
    await state.set_state(Trial.backpack_choice)


@initiation_router.callback_query(Trial.backpack_choice)
async def backpack_choice_handler(callback: types.CallbackQuery, state: FSMContext):
    """Handle backpack selection - prevent farming and award as unclaimed items"""
    data = await state.get_data()
    user_id = str(callback.from_user.id)
    username = data.get('username', callback.from_user.first_name)
    choice = callback.data
    
    # Get user to check if they already have a sector
    user = get_user(user_id)
    
    is_premium = choice == "backpack_premium"
    
    if is_premium:
        # Premium is locked - show alert but keep the keyboard so user can still pick Normal Backpack
        await callback.answer(
            "Payment system coming soon! Please choose the Normal Backpack for now.",
            show_alert=True
        )
        return
    
    # They chose the normal backpack
    await callback.answer("✅ Backpack equipped!")
    
    # PREVENT FARMING: Check if already completed tutorial
    if user and user.get("completed_tutorial"):
        # They've already done this tutorial - give no rewards
        sector_id = user.get("sector")
        all_sectors = load_sectors()
        info = all_sectors.get(int(sector_id), {}) if sector_id else {}
        sector_name = info.get("name", f"Sector {sector_id}") if info else f"Sector {sector_id}"
        sector_perks = info.get("perks", "") if info else ""

        await callback.message.edit_text(
            f"🃏 *GameMaster:* \"Trying to run the tutorial again, are we? How *delightfully* transparent.\"\n\n"
            f"\"I've already seen all your tricks. You get NOTHING this time.\"\n\n"
            f"📍 You're in: **#{sector_id} {sector_name.upper()}**\n"
            + (f"⚡ Perks: {sector_perks}\n" if sector_perks else "")
            + f"🎒 Backpack: Normal (5 slots)\n\n"
            f"Now run along. Type `!fusion` in the group when you're ready to play.",
            parse_mode="Markdown"
        )
        await state.clear()
        return
    
    # FIRST-TIME COMPLETION: Award unclaimed items
    
    # Get or assign sector (only assign if they don't already have one)
    all_sectors = load_sectors()
    if user and user.get("sector"):
        sector_id = user.get("sector")
        info = all_sectors.get(int(sector_id), {})
    else:
        # Assign random sector from 1-9 (starting sectors only)
        sector_id = random.randint(1, 9)
        info = all_sectors.get(sector_id, {})

    sector_name = info.get("name", f"Sector {sector_id}") if info else f"Sector {sector_id}"
    sector_env = info.get("environment", "") if info else ""
    sector_perks = info.get("perks", "") if info else ""
    
    # Store sector as ID
    set_sector(user_id, sector_id)
    
    # Update backpack image in database (set to normal_backpack)
    # Mark tutorial as completed to prevent farming
    if user:
        user["backpack_image"] = "normal_backpack"
        user["backpack_slots"] = 5
        user["completed_tutorial"] = True  # PREVENT FARMING
        save_user(user_id, user)
    
    # Award rewards as UNCLAIMED items (players must claim to inventory)
    # Shield (1 day)
    add_unclaimed_item(user_id, "shield", 1)
    
    # Award 3 crates as UNCLAIMED: wood, bronze, iron
    crate_configs = [
        ("wood_crate", random.randint(50, 100)),      # Wood crate: 50-100 XP
        ("bronze_crate", random.randint(100, 150)),   # Bronze crate: 100-150 XP
        ("iron_crate", random.randint(150, 200))      # Iron crate: 150-200 XP
    ]
    
    for crate_type, xp_reward in crate_configs:
        add_unclaimed_item(user_id, crate_type, xp_reward)
    
    # Award FREE teleport item as UNCLAIMED
    add_unclaimed_item(user_id, "teleport", 1)
    
    # Try to add user to Checkmate HQ group
    try:
        if CHECKMATE_HQ_GROUP_ID != -1001234567890:  # Only if configured
            await bot.get_chat_member(CHECKMATE_HQ_GROUP_ID, int(user_id))
    except:
        # User not in group yet - would need manual invite
        pass
    
    # Show final tutorial completion message
    await callback.message.edit_text(
        f"✨ *TUTORIAL COMPLETE!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌍 **YOU ARE BEING DROPPED IN:**\n"
        f"📍 *#{sector_id} {sector_name.upper()}*\n"
        + (f"🗺️ {sector_env}\n" if sector_env else "")
        + (f"⚡ Perks: {sector_perks}\n" if sector_perks else "")
        + f"\n🎒 **BACKPACK TYPE:** Normal Backpack (5 slots)\n\n"
        f"🎁 **STARTER REWARDS WAITING:**\n"
        f"You've received 5 items! They are **unclaimed**.\n\n"
        f"📦 Items Awaiting:\n"
        f"🛡️ Shield (1-day, expires tomorrow)\n"
        f"🪵 Wood Crate (50-100 XP)\n"
        f"🥉 Bronze Crate (100-150 XP)\n"
        f"⚙️ Iron Crate (150-200 XP)\n"
        f"🌀 Teleport (Travel to sectors 1-9)\n\n"
        f"**WHAT TO DO NEXT:**\n"
        f"1️⃣ Send DM: `!claims` to see unclaimed items\n"
        f"2️⃣ Click [CLAIM] on each item to add to inventory\n"
        f"3️⃣ Send DM: `!inventory` to see claimed items\n"
        f"4️⃣ Go to **Checkmate HQ** group\n"
        f"5️⃣ Type `!fusion` to start playing!\n\n"
        f"🃏 *GameMaster:* \"Welcome to The 64, {username}. Try not to disappoint me.\"",
        parse_mode="Markdown"
    )
    
    await state.clear()


async def check_premium_timeout(user_id: str):
    """Check if premium upgrade has expired"""
    if user_id not in premium_timers:
        return False
    
    if datetime.now() > premium_timers[user_id]:
        # Premium expired, remove from tracking
        del premium_timers[user_id]
        return True  # True = premium has expired
    
    return False  # Still valid
