import asyncio
import random
import httpx
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
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
    save_user,
    calculate_level,
    check_level_up,
    add_unclaimed_item,
    get_unclaimed_items,
    claim_item,
    remove_unclaimed_item,
    award_powerful_locked_item
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

class GameEngine:
    def __init__(self):
        self.active = False
        self.word1 = ""
        self.word2 = ""
        self.letters = ""
        self.scores = {} 
        self.used_words = []
        self.timer = 180  # 3 minutes
        self.empty_rounds = 0
        self.guess_count = 0  # Track guesses for visual refresh
        self.message_count = 0  # Track messages to repeat words
        self.games_played = 0  # Track rounds for help message
        self.games_until_help = random.randint(3, 7)  # Random interval for help
        self.crates_dropping = 0  # Number of crates dropping this round
        self.crate_claimers = []  # Track who claimed crates
        self.crate_drop_message_id = None  # Store crate drop message ID for reactions

# Game engines for different group chats
# Key: chat_id, Value: GameEngine instance
active_games = {}

def get_or_create_engine(chat_id):
    """Get or create a game engine for a specific chat"""
    if chat_id not in active_games:
        active_games[chat_id] = GameEngine()
    return active_games[chat_id]

# --- 🛠️ THE MISSING FUNCTIONS ---

async def fetch_supabase_words():
    """Pulls two 7-letter words from your database."""
    headers = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
    url = f"{SUPABASE_URL}/rest/v1/Dictionary?word_length=eq.7&select=word&limit=1"
    async with httpx.AsyncClient() as client:
        try:
            r1 = await client.get(f"{url}&offset={random.randint(0, 500)}", headers=headers)
            r2 = await client.get(f"{url}&offset={random.randint(0, 500)}", headers=headers)
            w1 = r1.json()[0]['word'].upper() if r1.json() else "PLAYERS"
            w2 = r2.json()[0]['word'].upper() if r2.json() else "DANGERS"
            return w1, w2
        except:
            return "PLAYERS", "DANGERS"

def is_anagram(guess, letters_pool):
    """Checks if the guess can be formed from the 14 letters."""
    pool = list(letters_pool)
    for char in guess:
        if char in pool:
            pool.remove(char)
        else:
            return False
    return True

async def check_supabase_dict(word):
    """Checks if the word exists in the dictionary."""
    headers = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SUPABASE_URL}/rest/v1/Dictionary?word=eq.{word}&select=word", headers=headers)
        return len(r.json()) > 0

async def repost_words_periodically(message: types.Message, chat_id: int, engine: GameEngine):
    """Words repeat only after 10 messages now"""
    pass
def get_help_message():
    """Return help message with all available commands"""
    help_text = """🃏 *GameMaster:* \"Oh, look who's struggling already.\"

**COMMANDS** *(if your tiny brain can manage it)*
`!fusion` — Start the game. Shocking, I know.
`!start` — Force-start the tutorial anytime
`!changename NewName` — Change your username
`!tutorial` — Restart the entire tutorial
`!profile` — Check your level, XP bar, and stats
`!inventory` — View your claimed items (DM only)
`!claims` — Check unclaimed rewards (DM only) ⚠️
`!weekly` — See who's beating you this week
`!alltime` — All-time leaderboard. Spoiler: it's not you.

**PROGRESSION SYSTEM**
⭐ Earn XP from words (1 XP per point)
📊 Level up every 100 XP
🎁 Every level-up gives FREE super crates
💎 Multiplier items grant 2x boosts
🎯 Top 3 players per round win bonus crates
⚡ 20% chance crates drop mid-game = extra rewards

**INVENTORY & CLAIMS**
📦 Inventory: Claimed items only (limited slots)
⚠️ Claims: Unclaimed rewards waiting for you
💡 Click [CLAIM] to move items to inventory
🔒 Some items are locked - too powerful to use!
⬆️ Upgrade backpack to get more slots!

**HOW THIS WORKS** *(pay attention this time)*
1️⃣ I give you two 7-letter words. Try not to panic.
2️⃣ Type ANY word mashable from both. Yes, really.
3️⃣ Points = word length minus 2. Math. Horrifying, I know.
4️⃣ Duplicates in THIS round? Ignored. Use your brain.
5️⃣ Earn XP, level up, get rewards!

**THE CRUEL REALITY:**
💰 Silver? You'll earn some, maybe
📊 Weekly reset every Sunday at 00:00 UTC
⏱️ 3 minutes per round (not 1, so try to keep up)
🏆 You probably won't make it anyway
🔥 But hey, someone has to be second-to-last!

Go on. Try. Amuse me."""
    return help_text


# --- 🎰 THE HARVEST ENGINE ---

async def run_auto_harvest(message: types.Message, chat_id: int):
    """Run the game for a specific chat"""
    engine = get_or_create_engine(chat_id)
    engine.active = True
    engine.empty_rounds = 0
    
    while engine.active:
        # Reset Round
        engine.scores = {}
async def run_auto_harvest(message: types.Message, chat_id: int):
    """Run the game for a specific chat"""
    engine = get_or_create_engine(chat_id)
    engine.active = True
    engine.empty_rounds = 0
    
    while engine.active:
        # Reset Round
        engine.scores = {}
        engine.used_words = []
        engine.guess_count = 0
        engine.message_count = 0
        engine.word1, engine.word2 = await fetch_supabase_words()
        engine.letters = (engine.word1 + engine.word2).lower()

        # RANDOM CRATE DROP (20% chance per round)
        crate_drop_message = ""
        if random.random() < 0.2:  # 20% chance
            num_crates = random.randint(1, 2)
            crate_drop_message = f"\n\n🎁 *BONUS: {num_crates} CRATE(S) AVAILABLE!* appear in the middle of the battlefield!"
            engine.crates_dropping = num_crates
            engine.crate_claimers = []  # Track who will claim
        else:
            engine.crates_dropping = 0

        await message.answer(
            f"🃏 *GameMaster:* \"This is a new round... Try not to starve.\"\n\n"
            f"🎯 `The words to be guessed are {engine.word1}`  `{engine.word2}`\n"
            f"{crate_drop_message}",
            parse_mode="Markdown"
        )
        
        # 120 Second Countdown (2 minutes)
        # Simulate crate drop at 50 seconds mark
        if engine.crates_dropping > 0:
            await asyncio.sleep(50)
            # Send crate drop message and store message ID for reaction tracking
            crate_msg = await message.answer(
                f"⚡ *CRATE DROP!* The crates descend from the sky!*\n"
                f"🎁 Wonder what you need to do...",
                parse_mode="Markdown"
            )
            engine.crate_drop_message_id = crate_msg.message_id
            engine.crate_claimers = []  # Reset claimers for this round
            await asyncio.sleep(10)  # 10 seconds to claim via reaction
            await asyncio.sleep(60)  # Remaining time to 120s
        else:
            # No crates - but still announce 1 minute warning at 60s mark
            await asyncio.sleep(60)
            await message.answer(
                "⏱️ *GameMaster:* \"Only 1 minute left. Hurry up.\"",
                parse_mode="Markdown"
            )
            await asyncio.sleep(60)  # Final 60 seconds

        # Round Over Logic
        total_pts = sum(p['pts'] for p in engine.scores.values())
        sorted_scores = sorted(engine.scores.values(), key=lambda x: x['pts'], reverse=True)
        
        # Leaderboard Text
        lead_text = "🏆 *ROUND OVER*\n━━━━━━━━━━━━━━━\n"
        if not sorted_scores:
            lead_text += "Nobody scored. Pathetic. This was boring"
            engine.empty_rounds += 1
        else:
            engine.empty_rounds = 0 # Reset if people play
            
            # Award crates to those who reacted if crates were dropping
            if engine.crates_dropping > 0 and len(engine.crate_claimers) > 0:
                for claimer in engine.crate_claimers:
                    user_id = str(claimer['user_id'])
                    add_unclaimed_item(user_id, "super_crate", 1)
                
                crate_count = len(engine.crate_claimers)
                lead_text += f"\n🎁 *CRATE AWARDS*:\n{crate_count} player(s) reacted fast and claimed **Super Crate(s)**!\n"
            
            for i, p in enumerate(sorted_scores):
                medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i+1}."
                lead_text += f"{medal} {p['name']} — {p['pts']} pts\n"
        
        lead_text += "\n📊 Use `!weekly` or `!alltime` for full stats"

        await message.answer(lead_text, parse_mode="Markdown")

        # LEVEL-UP CELEBRATIONS
        level_ups = []
        for user_id, score_data in engine.scores.items():
            if score_data.get("leveled_up"):
                user = get_user(user_id)
                if user:
                    current_level = user.get('level', 1)
                    level_up_msg = (
                        f"🎊 *LEVEL UP!* Congratulations, {score_data['name']}!\n"
                        f"You've reached **LEVEL {current_level}**!\n\n"
                        f"🃏 *GameMaster:* \"Oh, so you managed not to embarrass yourself this round. "
                        f"How *delightfully* surprising. Here's your pathetic reward.\"\n\n"
                        f"✨ You've received bonus items! Use `!claims` to view them."
                    )
                    
                    # Award bonus crates on level-up
                    add_unclaimed_item(user_id, "super_crate", 1)
                    
                    # Award random multiplier bonus
                    if random.random() < 0.5:
                        add_unclaimed_item(user_id, "xp_multiplier", 1, multiplier_value=2)
                    else:
                        add_unclaimed_item(user_id, "silver_multiplier", 1, multiplier_value=2)
                    
                    # Award powerful locked item every 5 levels (milestone)
                    if current_level % 5 == 0:
                        item_name, item_desc = award_powerful_locked_item(user_id)
                        level_up_msg += (
                            f"\n\n⚡ *MILESTONE REACHED!*\n"
                            f"You've unlocked: *{item_name}*\n"
                            f"_{item_desc}_\n\n"
                            f"⚠️ This item is too powerful to use, but too valuable to discard...\n"
                            f"💡 Hint: You might want a bigger backpack!"
                        )
                    
                    await message.answer(level_up_msg, parse_mode="Markdown")

        # Dormancy Check: Shut down if 3 rounds pass with 0 points
        if engine.empty_rounds >= 3:
            engine.active = False
            await message.answer("🃏 *GameMaster:* \"Silence. I'm bored. Type `!fusion` when you actually want to play.\"")
            break
        
        # Increment games played counter
        engine.games_played += 1
        
        # Check if should show help message
        if engine.games_played >= engine.games_until_help:
            await message.answer(get_help_message(), parse_mode="Markdown")
            engine.games_until_help = engine.games_played + random.randint(3, 7)  # Next help in 3-7 more games
        
        await asyncio.sleep(30) # 30s Break between rounds

# --- 📩 MESSAGE HANDLERS ---

@dp.message(F.text == "!start")
async def manual_start(message: types.Message, state):
    """Start tutorial when typing !start in DM"""
    if message.chat.type != "private":
        await message.answer(
            "🃏 *GameMaster:* \"Message me privately, fool.\"",
            parse_mode="Markdown"
        )
        return
    
    # Check if already registered
    user_id = str(message.from_user.id)
    if get_user(user_id):
        await message.answer(
            "🃏 *GameMaster:* \"You're already here. Stop pestering me.\"",
            parse_mode="Markdown"
        )
        return
    
    # Trigger the tutorial
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from initiation import Trial
    
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
        "\"So tell me, little mortal: are you here to join **The 64**? Or did you just wander in by accident?\"",
        parse_mode="Markdown",
        reply_markup=markup
    )
    await state.set_state(Trial.awaiting_username)

@dp.message(F.text == "!weekly")
async def show_weekly(message: types.Message):
    """Display weekly leaderboard"""
    if not get_user(str(message.from_user.id)):
        await message.answer(
            "🃏 *GameMaster:* \"You're not registered. Message me privately to join.\"",
            parse_mode="Markdown"
        )
        return
    
    leaderboard = get_weekly_leaderboard()
    
    if not leaderboard:
        await message.answer(
            "🏆 *WEEKLY LEADERBOARD*\n━━━━━━━━━━━━━━━\nNo one has played yet.",
            parse_mode="Markdown"
        )
        return
    
    text = "🏆 *WEEKLY LEADERBOARD*\n━━━━━━━━━━━━━━━\n"
    for i, player in enumerate(leaderboard, 1):
        medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"{i}."
        text += f"{medal} {player['username']} — {player['points']} pts\n"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "!alltime")
async def show_alltime(message: types.Message):
    """Display all-time leaderboard"""
    if not get_user(str(message.from_user.id)):
        await message.answer(
            "🃏 *GameMaster:* \"You're not registered. Message me privately to join.\"",
            parse_mode="Markdown"
        )
        return
    
    leaderboard = get_alltime_leaderboard()
    
    if not leaderboard:
        await message.answer(
            "🏆 *ALL-TIME LEADERBOARD*\n━━━━━━━━━━━━━━━\nNo one has played yet.",
            parse_mode="Markdown"
        )
        return
    
    text = "🏆 *ALL-TIME LEADERBOARD*\n━━━━━━━━━━━━━━━\n"
    for i, player in enumerate(leaderboard, 1):
        medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"{i}."
        text += f"{medal} {player['username']} — {player['points']} pts\n"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "!fusion")
async def start_game(message: types.Message):
    """Start game - ONLY works in group chats"""
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer(
            "🃏 *GameMaster:* \"This is a group game, fool. Stop wasting my time in DMs.\"",
            parse_mode="Markdown"
        )
        return
    
    chat_id = message.chat.id
    engine = get_or_create_engine(chat_id)
    
    if not engine.active:
        asyncio.create_task(run_auto_harvest(message, chat_id))
    else:
        await message.answer(
            "🃏 *GameMaster:* \"The souls are already being harvested. Open your eyes.\"",
            parse_mode="Markdown"
        )


@dp.message_reaction()
async def on_message_reaction(event: types.MessageReactionUpdated):
    """Handle reactions to crate drop messages"""
    if not event.user_id:
        return
    
    chat_id = event.chat.id
    engine = get_or_create_engine(chat_id)
    
    # Check if this is a reaction to the crate drop message
    if engine.crate_drop_message_id == event.message_id and engine.crates_dropping > 0:
        # Check if user already claimed
        if event.user_id not in [claimer['user_id'] for claimer in engine.crate_claimers]:
            # Only first 3 can claim
            if len(engine.crate_claimers) < 3:
                engine.crate_claimers.append({'user_id': event.user_id, 'username': ''})


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def on_guess(message: types.Message):
    if message.text.startswith("!"): return
    
    chat_id = message.chat.id
    engine = get_or_create_engine(chat_id)
    
    guess = message.text.lower().strip()
    u_id = str(message.from_user.id)
    
    # Check if round is over
    if not engine.active:
        # Try to see if it looks like a guess
        if is_anagram(guess, engine.letters) and guess not in engine.used_words:
            await message.reply("🛑 The round has ended, are you slow in the head or something?")
        return
    
    # Auto-register if not already registered
    if not get_user(u_id):
        register_user(u_id, message.from_user.first_name)
    
    # Increment message counter
    engine.message_count += 1
    
    # Repost words after every 10 messages
    if engine.message_count >= 4:
        await message.answer(
            f"The words to be guessed are: `{engine.word1}` & `{engine.word2}`",
            parse_mode="Markdown"
        )
        engine.message_count = 0
    
    # Validate guess
    if is_anagram(guess, engine.letters) and guess not in engine.used_words:
        if await check_supabase_dict(guess):
            pts = len(guess) - 2
            engine.used_words.append(guess)
            engine.guess_count += 1  # Increment guess counter
            
            # Add points to both weekly and all-time
            add_points(u_id, pts, message.from_user.first_name)
            
            # Award XP to player (1 XP per point earned)
            add_xp(u_id, pts)
            
            # Check for level-up
            old_level, new_level = check_level_up(u_id)
            
            # Update round scores for display
            if u_id not in engine.scores:
                engine.scores[u_id] = {"pts": 0, "name": message.from_user.first_name, "leveled_up": False}
            engine.scores[u_id]["pts"] += pts
            
            # Send feedback to player
            feedback = f"You found the word {guess.upper()} +{pts} pts! ⭐ +{pts} XP"
            if old_level and new_level:
                feedback += f"\n🎊 *LEVEL UP!* {old_level} → {new_level}"
                engine.scores[u_id]["leveled_up"] = True
            
            await message.reply(feedback)
    else:
        # Check if word was already guessed
        if guess in engine.used_words:
            await message.reply(f"{guess.upper()} has already been guessed!")

@dp.message(F.text.startswith("!changename"))
async def change_name(message: types.Message):
    """Change player username"""
    if message.chat.type != "private":
        await message.answer(
            "🃏 *GameMaster:* \"Handle your personal affairs in private, fool.\"",
            parse_mode="Markdown"
        )
        return
    
    u_id = str(message.from_user.id)
    user = get_user(u_id)
    
    if not user:
        await message.answer(
            "🃏 *GameMaster:* \"You're not even registered. Go complete the tutorial first.\"",
            parse_mode="Markdown"
        )
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "🃏 *GameMaster:* \"Usage: `!changename NewName`\"",
            parse_mode="Markdown"
        )
        return
    
    new_name = parts[1].strip()[:20]
    old_name = user.get('username', message.from_user.first_name)
    
    if new_name.lower() == old_name.lower():
        await message.answer(
            f"🃏 *GameMaster:* \"You're already '{old_name}'. Are you that desperate to change nothing?\"",
            parse_mode="Markdown"
        )
        return
    
    # Update name in database - TODO: add update_username to database.py
    await message.answer(
        f"✅ Your name changed from *{old_name}* to *{new_name}*. Let's see if this *new* name is less pathetic.",
        parse_mode="Markdown"
    )

@dp.message(F.text == "!tutorial")
async def trigger_tutorial(message: types.Message, state):
    """Allow anyone to restart the tutorial"""
    if message.chat.type != "private":
        await message.answer(
            "🃏 *GameMaster:* \"Handle this in private.\"",
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

# --- NEW COMMANDS: INVENTORY, PROFILE, HELP, SHOP ---

@dp.message(F.text == "!inventory")
async def show_inventory(message: types.Message):
    """Show player's inventory with inline keyboard buttons"""
    # Reject if in group chat
    if message.chat.type != "private":
        await message.answer(
            "🃏 *GameMaster:* \"Look at this nomad trying to expose his secrets to the world! "
            "Message me privately, fool!\"",
            parse_mode="Markdown"
        )
        return
    
    user_id = str(message.from_user.id)
    inventory = get_inventory(user_id)
    
    if not inventory:
        await message.answer(
            "🃏 *GameMaster:* \"Your inventory is empty. How *pathetic*.\"",
            parse_mode="Markdown"
        )
        return
    
    # Create inline keyboard buttons for each item
    keyboard = []
    for i, item in enumerate(inventory, 1):
        item_type = item.get("type", "").lower()
        xp_reward = item.get("xp_reward", 0)
        
        # Determine emoji and button text based on item type
        if "wood" in item_type and "crate" in item_type:
            text = f"🪵 WOOD ({xp_reward}XP)"
            callback = f"open_{i-1}"
        elif "bronze" in item_type and "crate" in item_type:
            text = f"🥉 BRONZE ({xp_reward}XP)"
            callback = f"open_{i-1}"
        elif "iron" in item_type and "crate" in item_type:
            text = f"⚙️ IRON ({xp_reward}XP)"
            callback = f"open_{i-1}"
        elif item_type == "shield":
            text = "🛡️ SHIELD [LOCKED]"
            callback = f"use_{i-1}"
        elif item_type == "teleport":
            text = "🌀 TELEPORT (Choose Sector)"
            callback = f"teleport_{i-1}"
        else:
            text = f"❓ {item_type.upper()}"
            callback = f"use_{i-1}"
        
        keyboard.append([InlineKeyboardButton(text=text, callback_data=callback)])
    
    # Add slots info
    inv_display = (
        "📦 *YOUR INVENTORY*\n"
        "━━━━━━━━━━━━━━━\n"
        "💡 Click on items to use them\n"
        f"📊 Slots: {len(inventory)}/5\n\n"
        "**Your Items:**"
    )
    
    markup = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.answer(inv_display, reply_markup=markup, parse_mode="Markdown")


@dp.message(F.text == "!claims")
async def show_claims(message: types.Message):
    """Show player's unclaimed items that must be claimed to inventory"""
    # Reject if in group chat
    if message.chat.type != "private":
        await message.answer(
            "🃏 *GameMaster:* \"Look at this nomad trying to expose his secrets to the world! "
            "Message me privately, fool!\"",
            parse_mode="Markdown"
        )
        return
    
    user_id = str(message.from_user.id)
    unclaimed = get_unclaimed_items(user_id)
    
    if not unclaimed:
        await message.answer(
            "🃏 *GameMaster:* \"You have no unclaimed rewards. How *boring*.\"",
            parse_mode="Markdown"
        )
        return
    
    # Create inline keyboard buttons for each unclaimed item
    keyboard = []
    for item in unclaimed:
        item_type = item.get("type", "").lower()
        multiplier = item.get("multiplier_value", 0)
        
        # Determine emoji and button text based on item type
        if "xp_multiplier" in item_type:
            text = f"⚡ XP MULTIPLIER x{multiplier} [CLAIM]"
            callback = f"claim_{item.get('id')}"
        elif "silver_multiplier" in item_type:
            text = f"💎 SILVER MULTIPLIER x{multiplier} [CLAIM]"
            callback = f"claim_{item.get('id')}"
        elif "super_crate" in item_type:
            text = f"🎁 SUPER CRATE [CLAIM] ⭐"
            callback = f"claim_{item.get('id')}"
        elif "locked_" in item_type:
            # Powerful locked item
            item_names = {
                "locked_legendary_artifact": "⚔️ LEGENDARY ARTIFACT",
                "locked_mythical_crown": "👑 MYTHICAL CROWN",
                "locked_void_stone": "🌑 VOID STONE",
                "locked_eternal_flame": "🔥 ETERNAL FLAME",
                "locked_celestial_key": "🗝️ CELESTIAL KEY"
            }
            display_name = item_names.get(item_type, "🔒 LEGENDARY ITEM")
            text = f"{display_name} [TOO POWERFUL!]"
            callback = f"claim_{item.get('id')}"
        else:
            text = f"🎁 {item_type.upper()} [CLAIM]"
            callback = f"claim_{item.get('id')}"
        
        keyboard.append([InlineKeyboardButton(text=text, callback_data=callback)])
    
    # Add info about claims
    claims_display = (
        "🎁 *UNCLAIMED REWARDS*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ You have unclaimed items!\n"
        "💡 Click [CLAIM] to add to inventory\n"
        "❌ If you don't claim them, you'll lose them!\n\n"
        f"**Items Awaiting: {len(unclaimed)}**"
    )
    
    markup = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.answer(claims_display, reply_markup=markup, parse_mode="Markdown")


@dp.callback_query(F.data.startswith("claim_"))
async def claim_item_callback(query: types.CallbackQuery):
    """Handle claiming an unclaimed item"""
    user_id = str(query.from_user.id)
    item_id = int(query.data.split("_")[1])
    
    success, message_text = claim_item(user_id, item_id)
    
    if success:
        await query.answer(f"✅ {message_text}")
        # Refresh the claims list
        unclaimed = get_unclaimed_items(user_id)
        if not unclaimed:
            await query.message.edit_text(
                "🃏 *GameMaster:* \"All claimed. Good little minion.\"",
                parse_mode="Markdown"
            )
        else:
            # Rebuild the claims display
            await query.message.edit_text(
                "🎁 *UNCLAIMED REWARDS*\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ You have unclaimed items!\n"
                "💡 Click [CLAIM] to add to inventory\n\n"
                f"**Remaining: {len(unclaimed)} items**",
                parse_mode="Markdown"
            )
    else:
        await query.answer(f"❌ {message_text}", show_alert=True)


@dp.message(F.text.startswith("!profile"))
async def show_profile(message: types.Message):
    """Show player's profile (only their own, DM only) with level and XP bar"""
    # Reject if in group chat
    if message.chat.type != "private":
        await message.answer(
            "🃏 *GameMaster:* \"Look at this fool trying to expose their stats to the whole group! "
            "That's private information, you dimwit. Message me in DM.\"",
            parse_mode="Markdown"
        )
        return
    
    # Check if they're trying to view someone else's profile
    command_parts = message.text.split()
    
    if len(command_parts) > 1:
        # They're trying to view another player's profile
        target_name = command_parts[1]
        await message.answer(
            f"🃏 *GameMaster:* \"Why are you trying to get *super knowledge* of your fellow minions? "
            f"Mind your own business, fool. You have no business prying into {target_name}'s secrets.\"",
            parse_mode="Markdown"
        )
        return
    
    # They're viewing their own profile
    user_id = str(message.from_user.id)
    profile = get_profile(user_id)
    
    if not profile:
        await message.answer(
            "🃏 *GameMaster:* \"You haven't started the tutorial yet.\"",
            parse_mode="Markdown"
        )
        return
    
    # Create XP bar visualization
    xp_bar_length = 20
    filled = int((profile['xp_progress'] / profile['xp_needed']) * xp_bar_length) if profile['xp_needed'] > 0 else 0
    empty = xp_bar_length - filled
    xp_bar = f"{'█' * filled}{'░' * empty}"
    
    profile_display = (
        f"🃏 *GameMaster:* \"So, you want to check yourself out. How *delightfully* narcissistic.\"\n\n"
        f"👤 *PROFILE: {profile['username']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎖️ *LEVEL {profile['level']}*\n"
        f"⭐ XP: {profile['xp']} | [{xp_bar}] {profile['xp_progress']}/{profile['xp_needed']}\n\n"
        f"💰 Silver: {profile['silver']}\n"
        f"📍 Sector: {profile['sector'] or 'Not Assigned'}\n\n"
        f"📊 *STATS*\n"
        f"├─ Weekly Points: {profile['weekly_points']}\n"
        f"├─ All-Time Points: {profile['all_time_points']}\n"
        f"└─ Total Words: {profile['all_time_points']}\n\n"
        f"📦 *INVENTORY*\n"
        f"├─ Claimed: {profile['inventory_count']}/{profile['backpack_slots']} slots\n"
        f"├─ Unclaimed: {profile['unclaimed_count']} items ⚠️\n"
        f"└─ Crates: {profile['crate_count']} | Shields: {profile['shield_count']}"
    )
    
    await message.answer(profile_display, parse_mode="Markdown")


@dp.message(F.text == "!help")
async def show_help(message: types.Message):
    """Show help message"""
    help_msg = get_help_message()
    
    # Add inventory/profile/shop info
    help_msg += (
        "\n\n**NEW COMMANDS:**\n"
        "`!inventory` — View your crates and items (DM only)\n"
        "`!profile` — Check your XP, Silver, and stats\n"
        "`!open <num>` — Open a crate\n"
        "`!use <num>` — Use/consume an item\n"
        "`!upgrade` — Upgrade to Queen's Satchel (20 slots for 900 ₦) *[COMING SOON]*\n"
        "`!shop` — *Coming soon...*"
    )
    
    await message.answer(help_msg, parse_mode="Markdown")


@dp.message(F.text == "!shop")
async def show_shop(message: types.Message):
    """Shop is coming soon"""
    await message.answer(
        "🃏 *GameMaster:* \"The shop is still under construction. "
        "Patience, you impatient fool. We're refining it for monsters like you.\"",
        parse_mode="Markdown"
    )


@dp.message(F.text == "!upgrade")
async def upgrade_backpack_cmd(message: types.Message):
    """Upgrade backpack to premium - LOCKED, payment system not ready"""
    await message.answer(
        "🃏 *GameMaster:* \"The Queen's Satchel upgrade system is still under construction. \n\n"
        "When it's ready, you'll be able to upgrade your backpack from 5 to 20 slots for 900 Naira.\n\n"
        "For now, manage what you have or use those crates, you greedy fool.\"",
        parse_mode="Markdown"
    )


# --- CRATE OPENING ---

@dp.message(F.text.regexp(r"^!open\s+\d+$"))
async def crate_open_handler(message: types.Message):
    """Handle !open <num> command to open crates"""
    if message.chat.type != "private":
        await message.answer(
            "🃏 *GameMaster:* \"Handle this in private, fool.\"",
            parse_mode="Markdown"
        )
        return
    
    import re
    match = re.search(r'\d+', message.text)
    if match:
        crate_num = int(match.group()) - 1  # Convert to 0-indexed
        await open_crate(message, crate_num)

async def open_crate(message: types.Message, crate_index: int):
    """Open a crate and award XP"""
    user_id = str(message.from_user.id)
    inventory = get_inventory(user_id)
    
    if crate_index < 0 or crate_index >= len(inventory):
        await message.answer("🃏 *GameMaster:* \"Invalid crate.\"", parse_mode="Markdown")
        return
    
    crate = inventory[crate_index]
    
    if "crate" not in crate.get("type", "").lower():
        await message.answer("🃏 *GameMaster:* \"That's not a crate.\"", parse_mode="Markdown")
        return
    
    xp_reward = crate.get("xp_reward", 0)
    crate_type = crate.get("type", "unknown")
    
    # Award XP
    add_xp(user_id, xp_reward)
    remove_inventory_item(user_id, crate.get("id"))
    
    await message.answer(
        f"✨ **CRATE OPENED!**\n"
        f"📦 {crate_type.upper()}\n"
        f"+ {xp_reward} XP",
        parse_mode="Markdown"
    )


@dp.message(F.text.regexp(r"^!use\s+\d+$"))
async def use_item_handler(message: types.Message):
    """Handle !use <num> command to use items"""
    if message.chat.type != "private":
        await message.answer(
            "🃏 *GameMaster:* \"You can't display such power in public. Use items in private, fool.\"",
            parse_mode="Markdown"
        )
        return
    
    import re
    match = re.search(r'\d+', message.text)
    if match:
        item_num = int(match.group()) - 1  # Convert to 0-indexed
        await use_item(message, item_num)


async def use_item(message: types.Message, item_index: int):
    """Use an item from inventory"""
    user_id = str(message.from_user.id)
    inventory = get_inventory(user_id)
    
    if item_index < 0 or item_index >= len(inventory):
        await message.answer("🃏 *GameMaster:* \"Invalid item.\"", parse_mode="Markdown")
        return
    
    item = inventory[item_index]
    item_type = item.get("type", "unknown").lower()
    
    # Handle different item types
    if "crate" in item_type:
        # This should use !open instead
        await message.answer(
            "🃏 *GameMaster:* \"That's a crate. Use `!open <num>` to open it, you fool.\"",
            parse_mode="Markdown"
        )
        return
    
    elif item_type == "shield":
        # Shield can't be used yet
        await message.answer(
            "🃏 *GameMaster:* \"The shield system hasn't been developed yet. "
            "It sits in your inventory gathering dust. How fitting.\"",
            parse_mode="Markdown"
        )
        return
    
    await message.answer("🃏 *GameMaster:* \"Unknown item.\"", parse_mode="Markdown")

async def drop_random_crate(chat_id: int, group_message: types.Message):
    """Drop a random crate for first 3 people to claim"""
    crate_types = [
        ("🪵 WOOD CRATE", 50, 100),
        ("🥉 BRONZE CRATE", 100, 150),
        ("⚙️ IRON CRATE", 150, 200)
    ]
    
    crate_name, min_xp, max_xp = random.choice(crate_types)
    xp_reward = random.randint(min_xp, max_xp)
    crate_id = f"drop_{int(random.random() * 1000000)}"
    
    claimed_by = []
    
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎁 CLAIM {crate_name}", callback_data=f"claim_{crate_id}")]
    ])
    
    msg = await group_message.answer(
        f"🎉 **{crate_name} DROPPED!**\n"
        f"First 3 to claim get +{xp_reward} XP each!\n"
        f"⏱️ Expires in 30 seconds...",
        reply_markup=markup
    )


# --- INVENTORY ITEM CALLBACKS ---

@dp.callback_query(F.data.startswith("open_"))
async def open_crate_callback(callback: types.CallbackQuery):
    """Handle opening crates via inline keyboard"""
    import re
    match = re.search(r'\d+', callback.data)
    if match:
        crate_index = int(match.group())
        await open_crate(callback.message, crate_index)
        await callback.answer("✨ Crate opened!")


@dp.callback_query(F.data.startswith("use_"))
async def use_item_callback(callback: types.CallbackQuery):
    """Handle using items via inline keyboard"""
    import re
    match = re.search(r'\d+', callback.data)
    if match:
        item_index = int(match.group())
        await use_item(callback.message, item_index)
        await callback.answer()


@dp.callback_query(F.data.startswith("teleport_"))
async def teleport_callback(callback: types.CallbackQuery):
    """Handle teleport item - show sector selection"""
    import re
    match = re.search(r'\d+', callback.data)
    if not match:
        return
    
    item_index = int(match.group())
    user_id = str(callback.from_user.id)
    inventory = get_inventory(user_id)
    
    if item_index < 0 or item_index >= len(inventory):
        await callback.answer("Invalid item")
        return
    
    item = inventory[item_index]
    if item.get("type", "").lower() != "teleport":
        await callback.answer("That's not a teleport!")
        return
    
    await callback.answer("Choose your destination!")
    
    # Show sector selection UI
    all_sectors = load_sectors()
    
    # Create buttons for sectors 1-9 (unlocked)
    keyboard = []
    for sector_id in range(1, 10):
        sector_name = all_sectors.get(sector_id, f"Sector {sector_id}")
        keyboard.append([InlineKeyboardButton(
            text=f"#{sector_id} {sector_name}",
            callback_data=f"teleport_to_{sector_id}"
        )])
    
    # Add locked sectors info
    keyboard.append([InlineKeyboardButton(
        text="🔒 Sectors 10-64 (LOCKED)",
        callback_data="locked_sectors"
    )])
    
    markup = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await callback.message.answer(
        "🌀 *TELEPORT NETWORK*\n"
        "━━━━━━━━━━━━━━━\n"
        "Available destinations:\n\n"
        "_Choose where you want to go_",
        parse_mode="Markdown",
        reply_markup=markup
    )


@dp.callback_query(F.data.startswith("teleport_to_"))
async def teleport_destination(callback: types.CallbackQuery):
    """Handle actual teleportation"""
    import re
    match = re.search(r'\d+', callback.data)
    if not match:
        return
    
    sector_id = int(match.group())
    user_id = str(callback.from_user.id)
    
    # Validate sector is in allowed range
    if sector_id < 1 or sector_id > 9:
        await callback.answer("That sector is locked!")
        return
    
    # Update player's sector
    all_sectors = load_sectors()
    sector_name = all_sectors.get(sector_id, f"Sector {sector_id}")
    
    user = get_user(user_id)
    old_sector = user.get("sector", "Unknown")
    
    set_sector(user_id, sector_id)
    
    # Remove teleport from inventory - find and remove first teleport
    inventory = get_inventory(user_id)
    for item in inventory:
        if item.get("type", "").lower() == "teleport":
            remove_inventory_item(user_id, item.get("id"))
            break
    
    await callback.answer("✨ Teleported!")
    
    await callback.message.edit_text(
        f"✨ *TELEPORTATION COMPLETE*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📍 You have traveled to:\n"
        f"**#{sector_id} {sector_name.upper()}**\n\n"
        f"Your teleport has been consumed.",
        parse_mode="Markdown"
    )


@dp.callback_query(F.data == "locked_sectors")
async def locked_sectors_info(callback: types.CallbackQuery):
    """Show info about locked sectors"""
    await callback.answer(
        "Sectors 10-64 will unlock as you progress!",
        show_alert=True
    )


async def try_add_inventory_item(user_id: str, item_type: str, xp_reward: int = 0, expires_at=None):
    """Try to add item to inventory. If full, return False with message"""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    result = add_inventory_item(user_id, item_type, xp_reward, expires_at)
    
    if not result:
        # Inventory is full
        profile = get_profile(user_id)
        return False, (
            f"🃏 *GameMaster:* \"Oh well, look at that! All this loot, "
            f"and you're stuck with a *fanny pack*.\"\n\n"
            f"📦 Your inventory is FULL ({profile['inventory_count']}/5)!\n\n"
            f"**What will you do?**\n"
            f"• `/discard` - Drop items you don't need\n"
            f"• `!use <num>` - Use items to make room\n"
            f"• `!upgrade` - Get the **Queen's Satchel** for 900 ₦ (20 slots!) *[COMING SOON]*"
        )
    
    return True, None


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())