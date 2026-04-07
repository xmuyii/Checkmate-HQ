"""
WORD FUSION GAME ENGINE - aiogram implementation
Handles 60-second rounds, scoring, leaderboards
"""

import asyncio
import random
from datetime import datetime
from typing import Dict, List, Tuple
import httpx

SUPABASE_URL = 'https://basniiolppmtpzishhtn.supabase.co'.rstrip('/')
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJhc25paW9scHBtdHB6aXNoaHRuIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTQ3NjMwOCwiZXhwIjoyMDkxMDUyMzA4fQ.qrj1BO5dNilRDvgKtvTdwIWjBhFTRyGzuHPD271Xcac'

class WordFusionGame:
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.active = False
        self.word1 = ""
        self.word2 = ""
        self.combined_letters = ""
        self.round_scores = {}  # {username: score}
        self.used_words_this_round = set()
        self.used_words_all_time = set()
        self.round_number = 0
        self.last_activity = datetime.now()
        self.empty_rounds = 0
        self.weekly_stats = {}  # {username: {silver: int, xp: int, wins: int}}
    
    def reset_round(self):
        """Clear scores and words for next round"""
        self.round_scores = {}
        self.used_words_this_round = set()
        self.empty_rounds += 1
    
    async def fetch_two_words(self) -> Tuple[str, str]:
        """Fetch 2 random words from Supabase dictionary"""
        headers = {
            'apikey': SUPABASE_KEY,
            'Authorization': f'Bearer {SUPABASE_KEY}'
        }
        try:
            async with httpx.AsyncClient() as client:
                # Get first word (6-7 letters)
                r1 = await client.get(
                    f"{SUPABASE_URL}/rest/v1/Dictionary?word_length=gte.6&word_length=lte.7&select=word&limit=1&offset={random.randint(0, 1000)}",
                    headers=headers,
                    timeout=5.0
                )
                w1 = r1.json()[0]['word'].upper() if r1.json() else "SILENT"
                
                # Get second word
                r2 = await client.get(
                    f"{SUPABASE_URL}/rest/v1/Dictionary?word_length=gte.6&word_length=lte.7&select=word&limit=1&offset={random.randint(0, 1000)}",
                    headers=headers,
                    timeout=5.0
                )
                w2 = r2.json()[0]['word'].upper() if r2.json() else "STREAM"
                
                return w1, w2
        except:
            return "SILENT", "STREAM"
    
    async def validate_word(self, word: str) -> bool:
        """Check if word exists in dictionary"""
        headers = {
            'apikey': SUPABASE_KEY,
            'Authorization': f'Bearer {SUPABASE_KEY}'
        }
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/Dictionary?word=eq.{word.lower()}&select=word",
                    headers=headers,
                    timeout=5.0
                )
                return len(r.json()) > 0
        except:
            return True  # Assume valid if can't check
    
    def can_make_word(self, word: str, letter_pool: str) -> bool:
        """Check if word can be made from letter pool"""
        pool = list(letter_pool)
        for char in word:
            if char in pool:
                pool.remove(char)
            else:
                return False
        return len(word) >= 3  # Minimum 3 letters
    
    def get_word_points(self, word: str) -> int:
        """Calculate points for a word"""
        length = len(word)
        if length == 3:
            return 3
        elif length == 4:
            return 5
        elif length == 5:
            return 10
        elif length == 6:
            return 15
        elif length == 7:
            return 25
        else:  # 8+
            return 50
    
    async def submit_word(self, username: str, word: str) -> Tuple[bool, str]:
        """
        Submit a word and return (success, message)
        Returns: (is_valid, points_or_message)
        """
        word = word.upper().strip()
        
        # Validate length
        if len(word) < 3:
            return False, "Word too short (min 3 letters)"
        
        # Check if already used this round
        if word in self.used_words_this_round:
            if username in self.round_scores:
                self.round_scores[username] -= 2  # Penalty
            return False, f"❌ '{word}' already used this round (-2 Silver)"
        
        # Check if can be made from letters
        if not self.can_make_word(word, self.combined_letters):
            if username in self.round_scores:
                self.round_scores[username] -= 1  # Penalty
            return False, f"❌ '{word}' can't be made from these letters (-1 Silver)"
        
        # Validate word exists
        if not await self.validate_word(word):
            if username in self.round_scores:
                self.round_scores[username] -= 1
            return False, f"❌ '{word}' not in dictionary (-1 Silver)"
        
        # Valid word!
        points = self.get_word_points(word)
        self.used_words_this_round.add(word)
        self.used_words_all_time.add(word)
        
        if username not in self.round_scores:
            self.round_scores[username] = 0
        
        self.round_scores[username] += points
        self.last_activity = datetime.now()
        self.empty_rounds = 0
        
        return True, f"✅ '{word}' +{points} Silver"
    
    def get_top_10(self) -> List[Tuple[str, int]]:
        """Get top 10 scorers for this round"""
        sorted_scores = sorted(
            self.round_scores.items(),
            key=lambda x: x[1],
            reverse=True
        )
        return sorted_scores[:10]
    
    def distribute_silver(self) -> Dict[str, int]:
        """
        Distribute 1-50 Silver among top 10
        Return {username: silver_amount}
        """
        top_10 = self.get_top_10()
        silver_distribution = {}
        
        # Calculate total points
        total_points = sum(score for _, score in top_10)
        if total_points == 0:
            return {}
        
        # Distribute 1-50 Silver proportionally
        remaining_silver = 50
        for i, (username, points) in enumerate(top_10):
            if i < 9:  # Rank 1-9
                share = int((points / total_points) * 50)
                silver_distribution[username] = max(share, 1)  # Min 1 silver
                remaining_silver -= silver_distribution[username]
            else:  # Rank 10
                silver_distribution[username] = remaining_silver
        
        return silver_distribution
    
    def format_round_leaderboard(self) -> str:
        """Format top 10 leaderboard message"""
        top_10 = self.get_top_10()
        silver_dist = self.distribute_silver()
        
        msg = f"🏆 **ROUND #{self.round_number} COMPLETE**\n━━━━━━━━━━━━━━━\n"
        
        emojis = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for idx, (username, points) in enumerate(top_10):
            silver = silver_dist.get(username, 0)
            msg += f"{emojis[idx]} **{username}** — {points}pts → {silver}💰\n"
        
        remaining = len(self.round_scores) - 10
        if remaining > 0:
            msg += f"\n_{remaining} other players: 0 Silver_"
        
        return msg
    
    async def check_inactivity(self) -> bool:
        """
        Check if game should stop due to inactivity
        Returns True if game should stop
        """
        time_since_activity = (datetime.now() - self.last_activity).total_seconds()
        
        # Stop if no scores for 5 rounds + 5 minutes
        if self.empty_rounds >= 5 and time_since_activity > 300:
            return True
        
        return False


# Global game instances per chat
active_games = {}

def get_or_create_game(chat_id: int) -> WordFusionGame:
    """Get or create a game instance for a specific chat"""
    if chat_id not in active_games:
        active_games[chat_id] = WordFusionGame(chat_id)
    return active_games[chat_id]
