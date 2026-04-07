"""
WORD FUSION TELEGRAM HANDLERS - aiogram implementation
Integrates word_fusion.py with Telegram bot
"""

import asyncio
from datetime import datetime
from aiogram import Router, types, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from word_fusion import get_or_create_game, WordFusionGame
from database import add_silver, add_points

# Router for Word Fusion game
word_fusion_router = Router()

# Track active round timers
active_timers = {}

@word_fusion_router.message(F.text == "!fusion")
async def start_word_fusion(message: types.Message):
    """Start or join Word Fusion game"""
    chat_id = message.chat.id
    user_id = str(message.from_user.id)
    username = message.from_user.first_name or f"User{user_id}"
    
    game = get_or_create_game(chat_id)
    
    if game.active:
        await message.answer(
            f"🎮 **Word Fusion Round #{game.round_number} Active!**\n\n"
            f"Words: {game.word1} + {game.word2}\n"
            f"⏳ Round in progress..."
        )
        return
    
    # Start new game
    await message.answer(
        "🎮 **WORD FUSION GAME STARTING**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Find as many words as possible from 2 combined words.\n"
        "You have 60 seconds!\n\n"
        "⏳ Fetching words..."
    )
    
    # Fetch words
    word1, word2 = await game.fetch_two_words()
    game.word1 = word1
    game.word2 = word2
    game.combined_letters = word1 + word2
    game.round_number += 1
    game.active = True
    game.reset_round()
    
    # Announce words
    await message.answer(
        f"🎯 **WORD FUSION ROUND #{game.round_number}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"**WORD 1:** {word1}\n"
        f"**WORD 2:** {word2}\n\n"
        f"⏳ **60 SECONDS** — GO!\n"
        f"_(Type words naturally in chat)_"
    )
    
    # Start 60-second round
    asyncio.create_task(run_game_round(message.bot, chat_id, 60))


async def run_game_round(bot, chat_id: int, duration: int):
    """Run a 60-second game round"""
    game = get_or_create_game(chat_id)
    
    # Sleep for duration
    await asyncio.sleep(duration)
    
    # Round complete
    game.active = False
    
    # Get top 10 and distribute silver
    top_10 = game.get_top_10()
    
    if not top_10:
        game.empty_rounds += 1
        check_inactivity = await game.check_inactivity()
        
        if check_inactivity:
            await bot.send_message(
                chat_id,
                "🛑 **Game Dormant** — No activity for 5 rounds.\n"
                "Type `!fusion` to restart."
            )
            return
        
        # Continue next round
        await bot.send_message(
            chat_id,
            "⏱️ **Round Complete** — No submissions.\n"
            "Starting next round in 5 seconds..."
        )
        await asyncio.sleep(5)
        await bot.send_message(chat_id, "!fusion")
        return
    
    # Format leaderboard
    leaderboard = game.format_round_leaderboard()
    silver_dist = game.distribute_silver()
    
    # Award silver and XP to players
    for username, silver in silver_dist.items():
        try:
            # Find user_id from username (would need to query database)
            # For now, assume username is stored somewhere
            add_silver(username, silver)
            add_points(username, 25)  # 25 XP per top-10 placement
        except:
            pass
    
    # Send leaderboard
    await bot.send_message(chat_id, leaderboard, parse_mode="Markdown")
    
    # Check if should continue
    check_inactivity = await game.check_inactivity()
    if check_inactivity:
        await bot.send_message(
            chat_id,
            "🛑 **Game Dormant** — Type `!fusion` to restart."
        )
        return
    
    # Start next round
    await bot.send_message(
        chat_id,
        "⏳ **NEXT ROUND** in 5 seconds...\n"
        "Get ready!"
    )
    await asyncio.sleep(5)
    await bot.send_message(chat_id, "!fusion")


@word_fusion_router.message(StateFilter(None), F.chat.type == "supergroup")
async def word_submission(message: types.Message):
    """Handle word submissions during active game"""
    chat_id = message.chat.id
    user_id = str(message.from_user.id)
    username = message.from_user.first_name or f"User{user_id}"
    word = message.text.strip()
    
    game = get_or_create_game(chat_id)
    
    # Only process if game is active
    if not game.active:
        return
    
    # Ignore commands
    if word.startswith('/') or word.startswith('!'):
        return
    
    # Check if word looks like a valid submission (no spaces, reasonable length)
    if len(word) < 3 or ' ' in word or len(word) > 20:
        return
    
    # Submit word
    is_valid, result_msg = await game.submit_word(username, word)
    
    # Only reply on success to reduce spam
    if is_valid:
        await message.answer(result_msg)


@word_fusion_router.message(F.text == "!mystats")
async def show_personal_stats(message: types.Message):
    """Show player's weekly stats"""
    user_id = str(message.from_user.id)
    username = message.from_user.first_name or f"User{user_id}"
    
    # Would query database for weekly stats
    await message.answer(
        f"📊 **{username}'s WEEKLY STATS**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Silver earned: TBD\n"
        f"⚡ XP earned: TBD\n"
        f"🎮 Rounds played: TBD\n"
        f"🏆 Top 10 finishes: TBD\n"
        f"📈 Best round: TBD Silver\n\n"
        f"_Weekly resets every Sunday @00:00 UTC_"
    )


@word_fusion_router.message(F.text == "!leaderboard")
async def show_weekly_leaderboard(message: types.Message):
    """Show weekly leaderboard"""
    await message.answer(
        "📊 **WEEKLY LEADERBOARD** (Top 50)\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🥇 **Rank 1:** Player A — 5000 Silver, 500 XP\n"
        "🥈 **Rank 2:** Player B — 4500 Silver, 450 XP\n"
        "🥉 **Rank 3:** Player C — 4000 Silver, 400 XP\n\n"
        "_Full leaderboard would load from database_"
    )
