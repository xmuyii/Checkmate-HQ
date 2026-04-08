"""
fusion_handlers.py
──────────────────
Auxiliary Word Fusion handlers: leaderboard display, player stats.

NOTE: The core !fusion command, game loop, and word submission are all
handled in main.py via GameEngine.  This router must NOT register a
handler for !fusion or for generic group messages, as that would
intercept traffic before main.py's handlers get a chance to run.
"""

from aiogram import Router, types, F
from database import get_weekly_leaderboard, get_alltime_leaderboard, get_user

word_fusion_router = Router()


@word_fusion_router.message(F.text == "!mystats")
async def show_personal_stats(message: types.Message):
    user_id  = str(message.from_user.id)
    username = message.from_user.first_name or f"User{user_id}"
    user     = get_user(user_id)

    if not user:
        await message.answer(
            "🃏 *GameMaster:* \"Who are you? No soul on record. "
            "Message me privately to register.\"",
            parse_mode="Markdown"
        )
        return

    await message.answer(
        f"📊 *{user.get('username', username)}'s STATS*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⭐ XP: {user.get('xp', 0)}\n"
        f"🎖️ Level: {(user.get('xp', 0) // 100) + 1}\n"
        f"💰 Silver: {user.get('silver', 0)}\n"
        f"📈 Weekly Points: {user.get('weekly_points', 0)}\n"
        f"🏆 All-Time Points: {user.get('all_time_points', 0)}\n"
        f"📝 Total Words: {user.get('total_words', 0)}\n\n"
        f"_Weekly resets every Sunday @00:00 UTC_",
        parse_mode="Markdown"
    )


@word_fusion_router.message(F.text == "!leaderboard")
async def show_leaderboard_alias(message: types.Message):
    """Alias for !weekly."""
    lb = get_weekly_leaderboard()
    if not lb:
        await message.answer(
            "🏆 *WEEKLY LEADERBOARD*\n━━━━━━━━━━━━━━━\nNo scores yet this week.",
            parse_mode="Markdown"
        )
        return
    text = "🏆 *WEEKLY LEADERBOARD*\n━━━━━━━━━━━━━━━\n"
    for i, p in enumerate(lb, 1):
        medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"{i}."
        text += f"{medal} {p['username']} — {p['points']} pts\n"
    await message.answer(text, parse_mode="Markdown")
