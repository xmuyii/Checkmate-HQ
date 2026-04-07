import json
import os
from datetime import datetime, timedelta

DB_FILE = "players.json"
SECTORS_FILE = "sectors.txt"

def load_data():
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

def save_user(user_id, data):
    all_data = load_data()
    all_data[str(user_id)] = data
    save_data(all_data)

def get_user(user_id):
    all_data = load_data()
    return all_data.get(str(user_id))

def load_sectors():
    """Load all sectors from sectors.txt"""
    sectors = {}
    if not os.path.exists(SECTORS_FILE):
        return sectors
    
    with open(SECTORS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    # Skip header row
    for line in lines[1:]:
        line = line.strip()
        if not line or line.startswith("SectorID"):
            continue
        
        parts = line.split("\t")
        if len(parts) >= 4:
            try:
                sector_id = int(parts[0])
                sector_name = parts[3]
                sectors[sector_id] = sector_name
            except:
                pass
    
    return sectors

def register_user(user_id, username):
    """Register a new player with default stats"""
    all_data = load_data()
    all_data[str(user_id)] = {
        "username": username,
        "all_time_points": 0,
        "weekly_points": 0,
        "week_start": get_current_week_start().isoformat(),
        "total_words": 0,
        "silver": 0,
        "xp": 0,
        "level": 1,
        "backpack_slots": 5,
        "backpack_image": "normal_backpack",  # or "queens_satchel" for premium
        "inventory": [],
        "unclaimed_items": [],  # Items that must be claimed before use
        "sector": None,
        "registered": True,
        "completed_tutorial": False,  # Prevent tutorial farming
        "last_level": 1  # Track previous level for level-up detection
    }
    save_data(all_data)

def get_current_week_start():
    """Get Monday of current week (week resets Sunday 11:59 PM)"""
    today = datetime.now()
    # Sunday = 6, so we go back to the Monday of this week
    days_since_monday = (today.weekday() + 1) % 7
    return (today - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)

def add_points(user_id, points, username):
    """Add points to weekly and all-time totals"""
    all_data = load_data()
    
    if str(user_id) not in all_data:
        register_user(user_id, username)
        all_data = load_data()
    
    user = all_data[str(user_id)]
    current_week = get_current_week_start().isoformat()
    
    # Reset weekly if it's a new week
    if user.get("week_start") != current_week:
        user["weekly_points"] = 0
        user["week_start"] = current_week
    
    user["all_time_points"] = user.get("all_time_points", 0) + points
    user["weekly_points"] = user.get("weekly_points", 0) + points
    user["total_words"] = user.get("total_words", 0) + 1
    
    save_data(all_data)

def get_weekly_leaderboard():
    """Get top 10 players this week"""
    all_data = load_data()
    players = []
    
    for user_id, data in all_data.items():
        players.append({
            "id": user_id,
            "username": data.get("username", "Unknown"),
            "points": data.get("weekly_points", 0)
        })
    
    return sorted(players, key=lambda x: x["points"], reverse=True)[:10]

def get_alltime_leaderboard():
    """Get top 10 players all time"""
    all_data = load_data()
    players = []
    
    for user_id, data in all_data.items():
        players.append({
            "id": user_id,
            "username": data.get("username", "Unknown"),
            "points": data.get("all_time_points", 0),
            "words": data.get("total_words", 0)
        })
    
    return sorted(players, key=lambda x: x["points"], reverse=True)[:10]

def add_silver(user_id, amount, username):
    """Add silver currency to player"""
    user = get_user(user_id)
    if not user:
        register_user(user_id, username)
        user = get_user(user_id)
    
    user["silver"] = user.get("silver", 0) + amount
    save_user(user_id, user)

def set_sector(user_id, sector):
    """Set player's sector"""
    user = get_user(user_id)
    if user:
        user["sector"] = sector
        save_user(user_id, user)

def update_username(user_id, new_username):
    """Update player's username"""
    user = get_user(user_id)
    if user:
        user["username"] = new_username
        save_user(user_id, user)
        return True
    return False

def upgrade_backpack(user_id):
    """Upgrade backpack to premium (900 naira cost)"""
    user = get_user(user_id)
    if user and user.get("silver", 0) >= 900:
        user["silver"] -= 900
        user["backpack_slots"] = 20
        user["backpack_image"] = "queens_satchel"
        save_user(user_id, user)
        return True
    return False

def add_inventory_item(user_id, item_type, xp_reward=0, expires_at=None):
    """Add item to inventory (crate, shield, etc.)"""
    user = get_user(user_id)
    if not user:
        return False
    
    inventory = user.get("inventory", [])
    slots = user.get("backpack_slots", 5)
    
    if len(inventory) >= slots:
        return False  # Inventory full
    
    # Generate unique ID based on current inventory size
    item_id = len(inventory)
    
    item = {
        "id": item_id,
        "type": item_type,
        "xp_reward": xp_reward,
        "expires_at": expires_at,  # For shields, this is expiration time
        "created_at": datetime.now().isoformat()
    }
    
    inventory.append(item)
    user["inventory"] = inventory
    save_user(user_id, user)
    return True

def remove_inventory_item(user_id, item_id):
    """Remove item from inventory"""
    user = get_user(user_id)
    if not user:
        return False
    
    inventory = user.get("inventory", [])
    user["inventory"] = [item for item in inventory if item.get("id") != item_id]
    save_user(user_id, user)
    return True

def get_inventory(user_id):
    """Get player's inventory"""
    user = get_user(user_id)
    if not user:
        return []
    return user.get("inventory", [])

def add_xp(user_id, amount):
    """Add XP to player"""
    user = get_user(user_id)
    if not user:
        return False
    
    user["xp"] = user.get("xp", 0) + amount
    save_user(user_id, user)
    return True

def use_xp(user_id, amount):
    """Use/deduct XP"""
    user = get_user(user_id)
    if not user:
        return False
    
    current_xp = user.get("xp", 0)
    if current_xp < amount:
        return False
    
    user["xp"] = current_xp - amount
    save_user(user_id, user)
    return True

def use_silver(user_id, amount):
    """Use/deduct silver"""
    user = get_user(user_id)
    if not user:
        return False
    
    current_silver = user.get("silver", 0)
    if current_silver < amount:
        return False
    
    user["silver"] = current_silver - amount
    save_user(user_id, user)
    return True

def get_profile(user_id):
    """Get player profile info with level and XP"""
    user = get_user(user_id)
    if not user:
        return None
    
    inventory = user.get("inventory", [])
    unclaimed = user.get("unclaimed_items", [])
    current_xp = user.get("xp", 0)
    level = calculate_level(current_xp)
    xp_for_level = get_xp_for_level(level)
    xp_for_next = get_xp_for_level(level + 1)
    xp_progress = current_xp - xp_for_level
    xp_needed = xp_for_next - xp_for_level
    
    crate_count = len([i for i in inventory if "crate" in i.get("type", "").lower()])
    shield_count = len([i for i in inventory if i.get("type", "").lower() == "shield"])
    
    return {
        "username": user.get("username"),
        "xp": current_xp,
        "level": level,
        "xp_progress": xp_progress,
        "xp_needed": xp_needed,
        "silver": user.get("silver", 0),
        "sector": user.get("sector"),
        "weekly_points": user.get("weekly_points", 0),
        "all_time_points": user.get("all_time_points", 0),
        "backpack_slots": user.get("backpack_slots", 5),
        "inventory_count": len(inventory),
        "unclaimed_count": len(unclaimed),
        "crate_count": crate_count,
        "shield_count": shield_count
    }

def calculate_level(xp):
    """Calculate level from XP (100 XP per level)"""
    return (xp // 100) + 1

def get_xp_for_level(level):
    """Get total XP needed to reach this level"""
    return (level - 1) * 100

def check_level_up(user_id):
    """Check if player leveled up and return old + new level"""
    user = get_user(user_id)
    if not user:
        return None, None
    
    old_level = user.get("last_level", 1)
    new_level = calculate_level(user.get("xp", 0))
    
    if new_level > old_level:
        user["last_level"] = new_level
        save_user(user_id, user)
        return old_level, new_level
    
    return None, None

def add_unclaimed_item(user_id, item_type, amount=1, multiplier_value=None):
    """Add unclaimed item (must be claimed before use)"""
    user = get_user(user_id)
    if not user:
        return False
    
    unclaimed = user.get("unclaimed_items", [])
    
    item = {
        "id": len(unclaimed),
        "type": item_type,
        "amount": amount,
        "multiplier_value": multiplier_value,  # For multiplier items
        "created_at": datetime.now().isoformat()
    }
    
    unclaimed.append(item)
    user["unclaimed_items"] = unclaimed
    save_user(user_id, user)
    return True

def get_unclaimed_items(user_id):
    """Get player's unclaimed items"""
    user = get_user(user_id)
    if not user:
        return []
    return user.get("unclaimed_items", [])

def claim_item(user_id, item_id):
    """Claim an unclaimed item to inventory"""
    user = get_user(user_id)
    if not user:
        return False, "Not registered"
    
    unclaimed = user.get("unclaimed_items", [])
    item = None
    
    # Find and remove from unclaimed
    for i, unc_item in enumerate(unclaimed):
        if unc_item.get("id") == item_id:
            item = unc_item
            unclaimed.pop(i)
            break
    
    if not item:
        return False, "Item not found"
    
    # Check if inventory has space
    inventory = user.get("inventory", [])
    slots = user.get("backpack_slots", 5)
    if len(inventory) >= slots:
        # Put item back in unclaimed
        unclaimed.append(item)
        user["unclaimed_items"] = unclaimed
        save_user(user_id, user)
        return False, "Inventory full"
    
    # Add to inventory
    new_item = {
        "id": len(inventory),
        "type": item.get("type"),
        "xp_reward": item.get("amount", 0),
        "multiplier_value": item.get("multiplier_value"),
        "created_at": item.get("created_at")
    }
    inventory.append(new_item)
    
    user["inventory"] = inventory
    user["unclaimed_items"] = unclaimed
    save_user(user_id, user)
    return True, "Item claimed successfully"

def remove_unclaimed_item(user_id, item_id):
    """Remove item from unclaimed (discard)"""
    user = get_user(user_id)
    if not user:
        return False
    
    unclaimed = user.get("unclaimed_items", [])
    user["unclaimed_items"] = [item for item in unclaimed if item.get("id") != item_id]
    save_user(user_id, user)
    return True

def award_powerful_locked_item(user_id):
    """Award a rare powerful item that can't be used - appears every 5 levels"""
    powerful_items = [
        ("legendary_artifact", "⚔️ LEGENDARY ARTIFACT", "An ancient weapon of unimaginable power. You can feel its raw energy."),
        ("mythical_crown", "👑 MYTHICAL CROWN", "The crown of a forgotten god. Its beauty is almost unbearable."),
        ("void_stone", "🌑 VOID STONE", "A stone from beyond the stars. It defies your understanding."),
        ("eternal_flame", "🔥 ETERNAL FLAME", "A flame that never dies. Holds secrets of the universe."),
        ("celestial_key", "🗝️ CELESTIAL KEY", "A key to dimensions you cannot yet comprehend."),
    ]
    
    import random
    item_type, display_name, desc = random.choice(powerful_items)
    
    add_unclaimed_item(user_id, f"locked_{item_type}", 1, None)
    return display_name, desc