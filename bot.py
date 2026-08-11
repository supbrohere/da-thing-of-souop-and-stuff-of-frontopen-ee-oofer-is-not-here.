import discord
from discord.ext import commands, tasks
import json
import os
import time
import random
import asyncio
import signal

from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

STARTING_TROOPS = 500
STARTING_TROOP_CAP = 5000
TROOP_REGEN_FLOOR = 1.1
TROOP_REGEN_MULTIPLIER = 1.5
CITY_TROOP_CAP_BONUS = 1500
CITY_PRICES = [250, 500, 500, 750, 1000, 1000, 1500, 1500, 2000, 2000, 2000, 5000, 5000, 5000, 5000, 5000, 7000, 7000, 7000, 7000, 10000]
BASE_GOLD_REGEN = 20
GOLD_REGEN_PER_CITY = 5
PORT_PRICES = [500, 750, 1000, 1000, 1500, 1500, 2000, 2000, 2000, 5000, 5000, 5000, 5000, 5000, 7500, 7500, 7500, 7500, 10000]
PORT_GOLD_REGEN_PER_PORT = 20
PORT_ALLIANCE_BONUS_PERCENT = 20
STARTING_STACK_COUNT = 3
MAX_STACK_COUNT = 6
STACK_PRICES = [20000, 40000, 65000]
SILO_PRICES = [9000, 15000, 20000, 25000, 35000, 50000, 50000, 50000, 100000]
MISSILE_TYPES = {
    "atom_bomb": {"label": "Atom Bomb", "emoji": "☢️", "price": 15000, "structure_destroy_percent": 0.50, "troop_destroy_percent": 0.0},
    "hydro_bomb": {"label": "Hydrogen Bomb", "emoji": "💥", "price": 45000, "structure_destroy_percent": 0.75, "troop_destroy_percent": 0.50},
}
TICK_MINUTES = 5
TICK_SECONDS = TICK_MINUTES * 60

DEFENDER_LOSS_MIN = 0.60
DEFENDER_LOSS_MAX = 1.20
ATTACKER_LOSS_MIN = 0.70
ATTACKER_LOSS_MAX = 1.00
BASE_CAPTURE_CHANCE = 0.30
HIGH_CAPTURE_CHANCE = 0.75
ZERO_TROOP_CAPTURE_BASE = 2
ATTACK_PERCENT_OPTIONS = [0.15, 0.30, 0.50, 0.75, 1.0]
ATTACK_PERCENT_LABELS = {0.15: "15%", 0.30: "30%", 0.50: "50%", 0.75: "75%", 1.0: "All In"}
ALLIANCE_DURATION_OPTIONS_DAYS = [2, 3, 4, 5]
BETRAYER_LOSS_MULTIPLIER = 1.5
BETRAYAL_DURATION_SECONDS = 18 * 3600
CLAN_TAG_MIN_LEN = 2
CLAN_TAG_MAX_LEN = 10
CLAN_DESC_MAX_LEN = 200
CLANS_PER_PAGE = 10
CLAN_MEMBERS_PER_PAGE = 10
CLAN_BANK_CAP = 30000
CLAN_WAR_POINTS_TO_WIN = 100
CLAN_WAR_POINTS_PER_ATTACK = 5
CLAN_WAR_POINTS_PER_MISSILE_HIT = 10
CLAN_WAR_POINTS_PER_ELIMINATION = 25
CLAN_WAR_REWARD_GOLD = 50000
SPAWN_IMMUNITY_SECONDS = 6 * 3600
ATTACK_BUFF_DAMAGE_MULTIPLIER = 1.5
ATTACK_BUFF_DURATION_SECONDS = 6 * 3600
SILO_COOLDOWN_SECONDS = 2 * 3600
SAM_PRICES = [20000, 45000, 80000, 130000, 190000]
SAM_MAX_LEVEL = 5
SAM_INTERCEPTOR_PRICE = 20000
SAM_COOLDOWN_SECONDS = 2 * 3600
STREAK_DAY_BOUNDARY_UTC_SECONDS = 22 * 3600
STREAK_BONUS_GOLD = 35000
RESPAWN_COOLDOWN_SECONDS = 2 * 3600
DATA_FILE = "game_data.json"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


def load_data():
    if not os.path.exists(DATA_FILE):
        return {"guilds": {}, "last_tick": time.time(), "clans": {}}
    with open(DATA_FILE, "r") as f:
        loaded = json.load(f)
    if "last_tick" not in loaded:
        loaded["last_tick"] = time.time()
    if "guilds" not in loaded:
        loaded["guilds"] = {}
    if "clans" not in loaded:
        loaded["clans"] = {}
    return loaded


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)


data = load_data()


def get_guild_players(guild_id):
    gid = str(guild_id)
    if gid not in data["guilds"]:
        data["guilds"][gid] = {"players": {}}
    return data["guilds"][gid]["players"]

def get_guild_dict(guild_id):
    gid = str(guild_id)
    if gid not in data["guilds"]:
        data["guilds"][gid] = {"players": {}}
    return data["guilds"][gid]


async def send_action_log(guild_id, embed):
    guild_dict = get_guild_dict(guild_id)
    channel_id = guild_dict.get("log_channel_id")
    if not channel_id:
        return
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        return
    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


def get_player(guild_id, user_id):
    players = get_guild_players(guild_id)
    return players.get(str(user_id))


def is_active_player(player):
    return player is not None and not player.get("eliminated", False)


def get_clans_dict():
    return data.setdefault("clans", {})


def get_clan(tag):
    if not tag:
        return None
    return get_clans_dict().get(tag.upper())


def find_clan_for_user(user_id):
    uid = str(user_id)
    for tag, clan in get_clans_dict().items():
        if uid in clan.get("members", []):
            return tag, clan
    return None, None


def in_clan(clan, user_id):
    return clan is not None and str(user_id) in clan.get("members", [])


async def parse_amount(interaction, raw):
    if not raw.isdigit() or int(raw) <= 0:
        await interaction.response.send_message("Enter a positive whole number.", ephemeral=True)
        return None
    return int(raw)


def clans_at_war(uid_a, uid_b):
    tag_a, clan_a = find_clan_for_user(uid_a)
    tag_b, _ = find_clan_for_user(uid_b)
    if clan_a is None or tag_b is None or tag_a == tag_b:
        return False
    return tag_b in clan_a.get("wars", {})


def declare_war(tag_a, tag_b):
    clan_a, clan_b = get_clan(tag_a), get_clan(tag_b)
    now = time.time()
    clan_a.setdefault("wars", {})[tag_b] = {"points": 0, "declared_at": now}
    clan_b.setdefault("wars", {})[tag_a] = {"points": 0, "declared_at": now}

    for guild_dict in data.get("guilds", {}).values():
        players = guild_dict.get("players", {})
        for uid_a in clan_a.get("members", []):
            pa = players.get(str(uid_a))
            if pa is None:
                continue
            for uid_b in clan_b.get("members", []):
                if str(uid_b) in pa.get("alliances", {}):
                    pa["alliances"].pop(str(uid_b), None)
                    pb = players.get(str(uid_b))
                    if pb is not None:
                        pb.get("alliances", {}).pop(str(uid_a), None)
    save_data(data)


def end_war(tag_a, tag_b):
    clan_a, clan_b = get_clan(tag_a), get_clan(tag_b)
    if clan_a is not None:
        clan_a.get("wars", {}).pop(tag_b, None)
    if clan_b is not None:
        clan_b.get("wars", {}).pop(tag_a, None)
    save_data(data)


def resolve_war_win(winner_tag, loser_tag):
    winner = get_clan(winner_tag)
    end_war(winner_tag, loser_tag)
    if winner is None:
        return
    for guild_dict in data.get("guilds", {}).values():
        for uid in winner.get("members", []):
            p = guild_dict.get("players", {}).get(str(uid))
            if is_active_player(p):
                p["gold"] = p.get("gold", 0) + CLAN_WAR_REWARD_GOLD
    save_data(data)


def award_war_points(attacker_uid, defender_uid, base_points, eliminated=False):
    a_tag, a_clan = find_clan_for_user(attacker_uid)
    d_tag, _ = find_clan_for_user(defender_uid)
    if a_clan is None or d_tag is None or a_tag == d_tag:
        return None
    war = a_clan.get("wars", {}).get(d_tag)
    if war is None:
        return None
    gained = base_points + (CLAN_WAR_POINTS_PER_ELIMINATION if eliminated else 0)
    war["points"] = war.get("points", 0) + gained
    total = war["points"]
    won = total >= CLAN_WAR_POINTS_TO_WIN
    if won:
        resolve_war_win(a_tag, d_tag)
    return {"clan": a_tag, "enemy": d_tag, "gained": gained, "total": total, "won": won}


def get_clan_bank_dict(clan):
    bank = clan.get("bank")
    if not isinstance(bank, dict):
        clan["bank"] = {}
        bank = clan["bank"]
    return bank


def get_clan_bank(clan, guild_id):
    return get_clan_bank_dict(clan).get(str(guild_id), 0)


def add_clan_bank(clan, guild_id, amount):
    bank = get_clan_bank_dict(clan)
    gid = str(guild_id)
    bank[gid] = bank.get(gid, 0) + amount


def sorted_clan_list(query=None):
    clans = get_clans_dict()
    items = list(clans.items())
    if query:
        q = query.upper()
        items = [(tag, clan) for tag, clan in items if q in tag]
    items.sort(key=lambda kv: (-len(kv[1].get("members", [])), kv[0]))
    return items


def make_clan_directory_embed(page, query=None):
    items = sorted_clan_list(query)
    total_pages = max(1, (len(items) + CLANS_PER_PAGE - 1) // CLANS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * CLANS_PER_PAGE
    page_items = items[start:start + CLANS_PER_PAGE]
    title = "🏰 Clan Directory" if not query else f"🏰 Clan Directory, search: \"{query}\""
    embed = discord.Embed(title=title, color=discord.Color.dark_gold())
    if not page_items:
        embed.description = "No clans found." if query else "No clans exist yet. Be the first to create one!"
    else:
        for tag, clan in page_items:
            status = "🔒 Invite Only" if clan.get("invite_only") else "🌐 Public"
            member_count = len(clan.get("members", []))
            embed.add_field(
                name=f"[{tag}] — {member_count} member{'s' if member_count != 1 else ''}",
                value=f"{clan.get('description', '(no description)')}\n{status}",
                inline=False
            )
    embed.set_footer(text=f"Page {page + 1}/{total_pages}")
    return embed, page, total_pages


def make_clan_hub_embed(guild_id, tag, clan, page=0, show_bank=True):
    leader_id = clan.get("leader_id")
    members = clan.get("members", [])

    def rank_key(uid):
        p = get_player(guild_id, uid)
        return p.get("troops", 0) if is_active_player(p) else -1

    ranked = sorted(members, key=rank_key, reverse=True)
    total_pages = max(1, (len(ranked) + CLAN_MEMBERS_PER_PAGE - 1) // CLAN_MEMBERS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * CLAN_MEMBERS_PER_PAGE
    page_members = ranked[start:start + CLAN_MEMBERS_PER_PAGE]

    title = f"🏰 [{tag}] Clan Hub" if show_bank else f"🏰 [{tag}] Clan"
    embed = discord.Embed(title=title, color=discord.Color.dark_gold())
    embed.description = clan.get("description", "(no description)")
    status = "🔒 Invite Only" if clan.get("invite_only") else "🌐 Public"
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Members", value=str(len(members)), inline=True)
    if show_bank:
        bank_amount = get_clan_bank(clan, guild_id)
        embed.add_field(name="Clan Bank (this server)", value=f"{bank_amount:,} / {CLAN_BANK_CAP:,} troops", inline=True)
    embed.add_field(name="Leader", value=f"<@{leader_id}>", inline=False)

    member_lines = []
    for uid in page_members:
        p = get_player(guild_id, uid)
        troops_text = f"{p.get('troops', 0):,} troops" if is_active_player(p) else "None"
        member_lines.append(f"<@{uid}> — {troops_text}")
    embed.add_field(
        name=f"Members in this server (Page {page + 1}/{total_pages})",
        value="\n".join(member_lines) if member_lines else "No members.",
        inline=False
    )
    return embed, page, total_pages


def make_clan_war_embed(tag, clan):
    embed = discord.Embed(title=f"⚔️ [{tag}] Wars", color=discord.Color.dark_red())
    wars = clan.get("wars", {})
    if not wars:
        embed.description = "Not currently at war with anyone."
    else:
        lines = []
        for enemy_tag, war in wars.items():
            enemy_clan = get_clan(enemy_tag)
            enemy_points = enemy_clan.get("wars", {}).get(tag, {}).get("points", 0) if enemy_clan else 0
            lines.append(
                f"**[{tag}] vs [{enemy_tag}]** — {war.get('points', 0)}/{CLAN_WAR_POINTS_TO_WIN} "
                f"vs {enemy_points}/{CLAN_WAR_POINTS_TO_WIN}"
            )
        embed.description = "\n".join(lines)
    return embed


def create_player(guild_id, user_id):
    players = get_guild_players(guild_id)
    uid = str(user_id)
    players[uid] = {
        "troops": STARTING_TROOPS,
        "troop_cap": STARTING_TROOP_CAP,
        "gold": 0,
        "cities": 0,
        "ports": 0,
        "silos": 0,
        "grid": [{"cities": 0, "ports": 0, "silo": 0, "missile": None, "cooldown_until": 0, "sam_level": 0, "sam_stock": 0, "sam_cooldown_until": 0, "sam_shots_fired": 0} for _ in range(STARTING_STACK_COUNT)],
        "eliminated": False,
        "alliances": {},
        "betrayer_until": 0,
        "immune_until": time.time() + SPAWN_IMMUNITY_SECONDS,
        "attack_buff_until": 0,
        "pending_free_silos": 0,
        "pending_free_ports": 0,
        "streak_number": 0,
        "streak_claimed_days": [],
        "streak_bonus_claimed": 0,
        "last_active_streak_day": None,
        "zero_troop_streaks": {}
    }
    save_data(data)
    return players[uid]


def active_alliance_count(player, now=None):
    if now is None:
        now = time.time()
    alliances = player.get("alliances", {})
    return sum(1 for expiry in alliances.values() if expiry > now)


def port_earnings_per_port(player, now=None):
    n = active_alliance_count(player, now)
    percent = 100 + (PORT_ALLIANCE_BONUS_PERCENT * n)
    return (PORT_GOLD_REGEN_PER_PORT * percent) // 100


def port_gold_regen_for(player, now=None):
    ports = player.get("ports", 0)
    if ports <= 0:
        return 0
    return ports * port_earnings_per_port(player, now)


def gold_regen_for(player, now=None):
    base = BASE_GOLD_REGEN + (player["cities"] * GOLD_REGEN_PER_CITY)
    return base + port_gold_regen_for(player, now)


def get_city_price(current_cities):
    if current_cities < len(CITY_PRICES):
        return CITY_PRICES[current_cities]
    return CITY_PRICES[-1]


def get_port_price(current_ports):
    if current_ports < len(PORT_PRICES):
        return PORT_PRICES[current_ports]
    return PORT_PRICES[-1]


def get_stack_price(current_stack_count):
    idx = current_stack_count - STARTING_STACK_COUNT
    if 0 <= idx < len(STACK_PRICES):
        return STACK_PRICES[idx]
    return None


def get_silo_price(current_silos):
    if current_silos < len(SILO_PRICES):
        return SILO_PRICES[current_silos]
    return SILO_PRICES[-1]


def ensure_grid(player):
    grid = player.get("grid")
    if isinstance(grid, list) and STARTING_STACK_COUNT <= len(grid) <= MAX_STACK_COUNT:
        for box in grid:
            box.setdefault("silo", 0)
            box.setdefault("missile", None)
            box.setdefault("cooldown_until", 0)
            box.setdefault("sam_level", 0)
            box.setdefault("sam_stock", 0)
            box.setdefault("sam_cooldown_until", 0)
            box.setdefault("sam_shots_fired", 0)
        player["silos"] = sum(1 for box in grid if box.get("silo", 0) > 0)
        return grid

    if isinstance(grid, list) and len(grid) > 0:
        total_cities = sum(box.get("cities", 0) for box in grid)
        total_ports = sum(box.get("ports", 0) for box in grid)
        total_silos = sum(1 for box in grid if box.get("silo", 0) > 0)
    else:
        total_cities = player.get("cities", 0)
        total_ports = player.get("ports", 0)
        total_silos = player.get("silos", 0)

    new_grid = [{"cities": 0, "ports": 0, "silo": 0, "missile": None, "cooldown_until": 0, "sam_level": 0, "sam_stock": 0, "sam_cooldown_until": 0, "sam_shots_fired": 0} for _ in range(STARTING_STACK_COUNT)]
    new_grid[0]["cities"] = total_cities
    new_grid[0]["ports"] = total_ports
    for i in range(min(total_silos, len(new_grid))):
        new_grid[i]["silo"] = 1
    player["grid"] = new_grid
    player["silos"] = sum(1 for box in new_grid if box.get("silo", 0) > 0)
    return new_grid


def transfer_structure_box(attacker, defender, structure_key):
    ensure_grid(attacker)
    ensure_grid(defender)
    for idx in range(len(defender["grid"])):
        if defender["grid"][idx][structure_key] > 0:
            defender["grid"][idx][structure_key] -= 1
            target_idx = idx if idx < len(attacker["grid"]) else len(attacker["grid"]) - 1
            attacker["grid"][target_idx][structure_key] += 1
            return
    attacker["grid"][0][structure_key] += 1


def resolve_missile_strike(defender, stack_idx, missile_key):
    info = MISSILE_TYPES[missile_key]
    box = defender["grid"][stack_idx]
    pool = (["cities"] * box["cities"]) + (["ports"] * box["ports"]) + (["silo"] * box.get("silo", 0))
    cities_destroyed = ports_destroyed = silos_destroyed = 0
    if pool:
        destroy_count = round(len(pool) * info["structure_destroy_percent"])
        random.shuffle(pool)
        destroyed = pool[:destroy_count]
        cities_destroyed = destroyed.count("cities")
        ports_destroyed = destroyed.count("ports")
        silos_destroyed = destroyed.count("silo")
        box["cities"] -= cities_destroyed
        box["ports"] -= ports_destroyed
        defender["cities"] -= cities_destroyed
        defender["ports"] = defender.get("ports", 0) - ports_destroyed
        if silos_destroyed:
            box["silo"] = 0
            box["missile"] = None
            defender["silos"] = max(0, defender.get("silos", 0) - silos_destroyed)
        if cities_destroyed:
            defender["troop_cap"] = max(STARTING_TROOP_CAP, defender["troop_cap"] - cities_destroyed * CITY_TROOP_CAP_BONUS)
            defender["troops"] = min(defender["troops"], defender["troop_cap"])

    troops_destroyed = 0
    if info.get("troop_destroy_percent", 0) > 0:
        troops_destroyed = round(defender["troops"] * info["troop_destroy_percent"])
        defender["troops"] -= troops_destroyed

    return cities_destroyed, ports_destroyed, silos_destroyed, troops_destroyed

def calculate_troop_regen(current_troops, troop_cap):
    if troop_cap <= 0 or current_troops >= troop_cap:
        return 0
    raw = (10 + (current_troops ** 0.73) / 4) * (1 - current_troops / troop_cap)
    return max(TROOP_REGEN_FLOOR, raw) * TROOP_REGEN_MULTIPLIER


def prune_alliances(player, now=None):
    if now is None:
        now = time.time()
    alliances = player.get("alliances", {})
    expired = [uid for uid, expiry in alliances.items() if expiry <= now]
    for uid in expired:
        del alliances[uid]
    return alliances


def form_alliance(guild_id, user_a_id, user_b_id, days):
    expiry = time.time() + days * 86400
    player_a = get_player(guild_id, user_a_id)
    player_b = get_player(guild_id, user_b_id)
    player_a.setdefault("alliances", {})[str(user_b_id)] = expiry
    player_b.setdefault("alliances", {})[str(user_a_id)] = expiry
    save_data(data)


def break_alliance(guild_id, user_a_id, user_b_id):
    player_a = get_player(guild_id, user_a_id)
    player_b = get_player(guild_id, user_b_id)
    if player_a is not None:
        player_a.get("alliances", {}).pop(str(user_b_id), None)
    if player_b is not None:
        player_b.get("alliances", {}).pop(str(user_a_id), None)
def is_betrayer(player, now=None):
    if now is None:
        now = time.time()
    return player.get("betrayer_until", 0) > now


def is_immune(player, now=None):
    if now is None:
        now = time.time()
    return player.get("immune_until", 0) > now


def has_admin_access(interaction: discord.Interaction):
    return interaction.permissions.manage_guild


def is_attack_buffed(player, now=None):
    if now is None:
        now = time.time()
    return player.get("attack_buff_until", 0) > now


def is_silo_on_cooldown(box, now=None):
    if now is None:
        now = time.time()
    return box.get("cooldown_until", 0) > now


def is_sam_on_cooldown(box, now=None):
    if now is None:
        now = time.time()
    return box.get("sam_cooldown_until", 0) > now


def can_sam_intercept(box, now=None):
    return box.get("sam_level", 0) > 0 and box.get("sam_stock", 0) > 0 and not is_sam_on_cooldown(box, now)


def try_intercept(defender, stack_idx):
    box = defender["grid"][stack_idx]
    if not can_sam_intercept(box):
        return False
    box["sam_stock"] -= 1
    box["sam_shots_fired"] = box.get("sam_shots_fired", 0) + 1
    if box["sam_shots_fired"] >= box.get("sam_level", 0) or box["sam_stock"] <= 0:
        box["sam_cooldown_until"] = time.time() + SAM_COOLDOWN_SECONDS
        box["sam_shots_fired"] = 0
    return True


def format_remaining(seconds):
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def get_streak_day_index(ts=None):
    if ts is None:
        ts = time.time()
    return int((int(ts) - STREAK_DAY_BOUNDARY_UTC_SECONDS) // 86400)


def update_daily_streak(player):
    today = get_streak_day_index()
    last = player.get("last_active_streak_day")
    if last == today:
        return
    if last is not None and today - last == 1:
        player["streak_number"] = player.get("streak_number", 0) + 1
    else:
        player["streak_number"] = 1
        player["streak_claimed_days"] = []
        player["streak_bonus_claimed"] = 0
    player["last_active_streak_day"] = today


def get_streak_reward_label(day_number):
    if day_number == 1:
        return "5,000 gold"
    if day_number == 2:
        return "a free Missile Silo"
    if day_number == 3:
        return "10,000 gold"
    if day_number == 4:
        return "6-hour +50% attack damage buff"
    if day_number == 5:
        return "10,000 gold"
    if day_number == 6:
        return "3 free Ports"
    if day_number == 7:
        return "full troop refill"
    return f"{STREAK_BONUS_GOLD:,} gold"


def apply_streak_reward(player, day_number):
    if day_number == 1:
        player["gold"] += 5000
    elif day_number == 2:
        player["pending_free_silos"] = player.get("pending_free_silos", 0) + 1
    elif day_number == 3:
        player["gold"] += 15000
    elif day_number == 4:
        player["attack_buff_until"] = time.time() + ATTACK_BUFF_DURATION_SECONDS
    elif day_number == 5:
        player["gold"] += 30000
    elif day_number == 6:
        player["pending_free_ports"] = player.get("pending_free_ports", 0) + 3
    elif day_number == 7:
        player["troops"] = player["troop_cap"]
    else:
        player["gold"] += STREAK_BONUS_GOLD


async def track_activity_for_streak(interaction: discord.Interaction) -> bool:
    if interaction.guild_id is not None:
        player = get_player(interaction.guild_id, interaction.user.id)
        if player is not None:
            update_daily_streak(player)
            save_data(data)
    return True


bot.tree.interaction_check = track_activity_for_streak

def process_ticks(now=None):
    if now is None:
        now = time.time()
    elapsed = now - data["last_tick"]
    ticks_passed = int(elapsed // TICK_SECONDS)
    if ticks_passed <= 0:
        return 0
    for guild_id, guild_data in data["guilds"].items():
        for user_id, player in guild_data["players"].items():
            if player.get("eliminated"):
                continue
            troops = player["troops"]
            cap = player["troop_cap"]
            for _ in range(ticks_passed):
                if troops >= cap:
                    break
                troops = min(troops + round(calculate_troop_regen(troops, cap)), cap)
            player["troops"] = troops
            player["gold"] += gold_regen_for(player) * ticks_passed
    data["last_tick"] += ticks_passed * TICK_SECONDS
    save_data(data)
    return ticks_passed


@tasks.loop(minutes=1)
async def tick_checker():
    process_ticks()


def make_status_embed(online=True):
    if online:
        embed = discord.Embed(
            description="🟢🟢🟢 **bot is online yay** 🟢🟢🟢",
            color=discord.Color.green()
        )
        embed.set_footer(text="This message updates itself now :)")
    else:
        embed = discord.Embed(
            description="🔴🔴🔴 **bot is offline rip** 🔴🔴🔴",
            color=discord.Color.red()
        )
        embed.set_footer(text="if the bot ever crashes or loses power it'll still say online.")
    return embed


async def set_status_all_guilds(online):
    for guild_id, guild_data in data["guilds"].items():
        channel_id = guild_data.get("status_channel_id")
        if not channel_id:
            continue
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            continue

        message = None
        message_id = guild_data.get("status_message_id")
        if message_id:
            try:
                message = await channel.fetch_message(int(message_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None

        try:
            if message is not None:
                await message.edit(embed=make_status_embed(online))
            else:
                new_message = await channel.send(embed=make_status_embed(online))
                guild_data["status_message_id"] = new_message.id
                save_data(data)
        except (discord.Forbidden, discord.HTTPException):
            continue


async def shutdown_and_exit():
    print("Shutting down, updating status message(s)...")
    try:
        await set_status_all_guilds(False)
    except Exception as e:
        print(f"Couldn't update status on shutdown: {e}")
    await bot.close()


def _handle_stop_signal(sig, frame):
    loop = getattr(bot, "loop", None)
    if loop and loop.is_running():
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(shutdown_and_exit()))
    else:
        raise KeyboardInterrupt


signal.signal(signal.SIGINT, _handle_stop_signal)
try:
    signal.signal(signal.SIGTERM, _handle_stop_signal)
except (AttributeError, ValueError):
    pass


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Error syncing commands: {e}")
    if not tick_checker.is_running():
        tick_checker.start()
    await set_status_all_guilds(True)


@bot.tree.command(name="joingame", description="Join da game")
async def joingame(interaction: discord.Interaction):
    existing = get_player(interaction.guild_id, interaction.user.id)
    if existing is not None:
        if existing.get("eliminated"):
            await interaction.response.send_message(
                "You were eliminated, use /respawn to get back in.", ephemeral=True
            )
        else:
            await interaction.response.send_message("You've already joined the game bro", ephemeral=True)
        return
    create_player(interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(
        f"Welcome, {interaction.user.mention}! You start with "
        f"{STARTING_TROOPS} troops (cap {STARTING_TROOP_CAP}) and 0 gold."
    )

@bot.tree.command(name="respawn", description="Rejoin the game after being eliminated (3 hour cooldown)")
async def respawn(interaction: discord.Interaction):
    player = get_player(interaction.guild_id, interaction.user.id)
    if player is None or not player.get("eliminated"):
        await interaction.response.send_message("You're not eliminated, no need to respawn.", ephemeral=True)
        return

    ready_at = player.get("eliminated_at", 0) + RESPAWN_COOLDOWN_SECONDS
    remaining = ready_at - time.time()
    if remaining > 0:
        hours = int(remaining // 3600)
        minutes = int((remaining % 3600) // 60)
        await interaction.response.send_message(
            f"You can respawn in {hours}h {minutes}m.", ephemeral=True
        )
        return

    player["troops"] = STARTING_TROOPS
    player["troop_cap"] = STARTING_TROOP_CAP
    player["gold"] = 0
    player["cities"] = 0
    player["ports"] = 0
    player["silos"] = 0
    player["grid"] = [{"cities": 0, "ports": 0, "silo": 0, "missile": None, "cooldown_until": 0, "sam_level": 0, "sam_stock": 0, "sam_cooldown_until": 0, "sam_shots_fired": 0} for _ in range(STARTING_STACK_COUNT)]
    player["eliminated"] = False
    player["immune_until"] = time.time() + SPAWN_IMMUNITY_SECONDS
    save_data(data)

    await interaction.response.send_message(
        f"✅ {interaction.user.mention} respawned with a fresh start "
        f"({STARTING_TROOPS} troops, cap {STARTING_TROOP_CAP}, 0 gold, 0 cities)."
    )

@bot.tree.command(name="help", description="How to use the bot")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="How to Play", color=0xFFFF00)
    embed.add_field(
        name="Getting Started",
        value=(
            "`/joingame` joins the leaderboard, unlocks all commands, starts you with a 5,000 troop capacity.\n"
            "Wait for gold to accumulate, then buy a city or port with `/gamehub` (build menu)."
        ),
        inline=False
    )
    embed.add_field(
        name="Structures",
        value=(
            "**Cities** - raises your troop capacity and generate extra gold per tick.\n"
            "**Ports** - adds +20 base gold per port. Each alliance you have adds +20% on top of that "
            "base 20 (e.g., 1 alliance = 24 gold/port). Scales fast with more ports."
        ),
        inline=False
    )
    embed.add_field(
        name="Stacks",
        value=(
            "You start with 3 stacks; buying more costs increasingly more gold (max 6). Each stack holds "
            "an unlimited amount of structures, with an exception of silos (1 per stack). "
            "*NOTE: Putting everything in one stack makes it an easy bombing target.*"
        ),
        inline=False
    )
    embed.add_field(
        name="Launching Missiles",
        value=(
            "Use the launch button in `/gamehub` to fire from a silo (If empty, you'll be prompted to buy a missile).\n"
            "Two missile types exist, both need a silo: **Atom Bomb** (15,000 gold) destroys 50% of structures "
            "in the targeted stack. **Hydrogen Bomb** (45,000 gold) destroys 75% of structures in the targeted "
            "stack and 50% of the target's total troops (skipped if intercepted).\n"
            "**Missiles** can only be loaded via the launch menu, not the build menu.\n"
            "Launching at an ally will trigger a **betrayal warning** (confirm/cancel) and applies the same "
            "18-hour betrayal effect as attacking them."
        ),
        inline=False
    )
    embed.add_field(
        name="Attacking",
        value=(
            "`/attack` allows you to attack someone. First you pick a target, then how much troops to send: "
            "**15%**, **30%**, **50%**, **75%**, or **100%** (all in). Each attack has a chance to destroy a structure on hit.\n"
            "Your target gets a DM with troops sent/lost. Losses are randomized: defenders can lose **60**-**120%** "
            "*(relative to attacker's sent troops);* attackers lose **70**-**100%** of their own.\n"
            "No structures + no troops = **eliminated**. Use `/respawn` after a 3 hour cooldown to rejoin."
        ),
        inline=False
    )
    embed.add_field(
        name="Commands",
        value=(
            "`/joingame` - Join the game\n"
            "`/respawn` - Rejoin after elimination (3h cooldown)\n"
            "`/gamehub` - Manage/view land, launch missiles\n"
            "`/smartbuild` - Bulk-build cities/ports, or auto-refill SAMs\n"
            "`/attack` - Attack a player\n"
            "`/leaderboard` - Ranked by current troops"
        ),
        inline=False
    )
    embed.add_field(
        name="Questions?",
        value="Ping an Admin in the [MOON](https://discord.gg/NnY7e739ue) server. Admins elsewhere likely won't know the bot as we do.",
        inline=False
    )
    await interaction.response.send_message(embed=embed)


def make_hub_embed(member, player):
    embed = discord.Embed(title=f"{member.display_name}'s land", color=discord.Color.gold())
    embed.add_field(name="Troops", value=f"{player['troops']:,} / {player['troop_cap']:,}", inline=True)
    embed.add_field(name="Gold", value=f"{player['gold']:,}", inline=True)
    embed.add_field(name="Cities", value=str(player["cities"]), inline=True)

    ports = player.get("ports", 0)
    if ports > 0:
        n = active_alliance_count(player)
        percent = 100 + (PORT_ALLIANCE_BONUS_PERCENT * n)
        per_port = port_earnings_per_port(player)
        ports_value = f"{ports} ({percent}%)"
    else:
        ports_value = "0"
    embed.add_field(name="Ports", value=ports_value, inline=True)

    embed.add_field(name="Gold per tick", value=str(gold_regen_for(player)), inline=True)

    now = time.time()
    active_effects = []
    if is_immune(player, now):
        active_effects.append(f"🛡 Spawn Immunity: {format_remaining(player['immune_until'] - now)} left")
    if is_attack_buffed(player, now):
        bonus_pct = int((ATTACK_BUFF_DAMAGE_MULTIPLIER - 1) * 100)
        active_effects.append(f"⚔️ Attack Buff (+{bonus_pct}% dmg), {format_remaining(player['attack_buff_until'] - now)} left")
    if is_betrayer(player, now):
        bonus_pct = int((BETRAYER_LOSS_MULTIPLIER - 1) * 100)
        active_effects.append(f"🗡 Betrayer Debuff (+{bonus_pct}% dmg taken), {format_remaining(player['betrayer_until'] - now)} left")
    if active_effects:
        embed.add_field(name="Active Effects", value="\n".join(active_effects), inline=False)

    pending_lines = []
    if player.get("pending_free_silos", 0) > 0:
        pending_lines.append(f"🎁 {player['pending_free_silos']} free Missile Silo(s), place with Build")
    if player.get("pending_free_ports", 0) > 0:
        pending_lines.append(f"🎁 {player['pending_free_ports']} free Port(s), place with Build")
    if pending_lines:
        embed.add_field(name="Unclaimed Rewards", value="\n".join(pending_lines), inline=False)

    embed.set_footer(text=f" Regeneration/tick happens every {TICK_MINUTES} minutes irl.")
    return embed


def make_alliance_embed(guild_id, member, player):
    alliances = prune_alliances(player)
    embed = discord.Embed(title=f"{member.display_name}'s Alliances", color=discord.Color.blurple())
    if alliances:
        now = time.time()
        lines = []
        for uid, expiry in alliances.items():
            remaining = expiry - now
            days_left = int(remaining // 86400)
            hours_left = int((remaining % 86400) // 3600)
            ally_player = get_player(guild_id, uid)
            name = f"<@{uid}>"
            if ally_player and ally_player.get("eliminated"):
                name += " (eliminated)"
            lines.append(f"• {name} — expires in {days_left}d {hours_left}h")
        embed.description = "\n".join(lines)
    else:
        embed.description = f"{member.display_name} has no active alliances."
    return embed


def make_grid_embed(member, player, editable=True):
    ensure_grid(player)
    embed = discord.Embed(
        title=f"{member.display_name}'s Building Stacks",
        color=discord.Color.gold()
    )
    embed.add_field(name="💰 Gold", value=f"{player['gold']:,}", inline=False)
    for idx, box in enumerate(player["grid"]):
        if box.get("silo", 0) > 0:
            if is_silo_on_cooldown(box):
                remaining = format_remaining(box["cooldown_until"] - time.time())
                silo_line = f"🚀 Silo: Cooldown ({remaining})"
            elif box.get("missile"):
                silo_line = f"🚀 Silo: Loaded ({MISSILE_TYPES[box['missile']]['label']})"
            else:
                silo_line = "🚀 Silo: Empty"
        else:
            silo_line = "🚀 Silo: No"

        sam_level = box.get("sam_level", 0)
        if sam_level > 0:
            stock = box.get("sam_stock", 0)
            if is_sam_on_cooldown(box):
                remaining = format_remaining(box["sam_cooldown_until"] - time.time())
                sam_line = f"🛡 SAM Lvl {sam_level}: Cooldown ({remaining}) [{stock}/{sam_level} loaded]"
            else:
                sam_line = f"🛡 SAM Lvl {sam_level}: {stock}/{sam_level} loaded"
        else:
            sam_line = "🛡 SAM: No"

        embed.add_field(
            name=f"Stack {idx + 1}",
            value=f"🏙 Cities: {box['cities']}\n⚓ Ports: {box['ports']}\n{silo_line}\n{sam_line}",
            inline=True
        )
    embed.add_field(name="Total Cities", value=str(player["cities"]), inline=True)
    embed.add_field(name="Total Ports", value=str(player.get("ports", 0)), inline=True)
    embed.add_field(name="Total Silos", value=str(player.get("silos", 0)), inline=True)
    embed.add_field(name="Stacks Unlocked", value=f"{len(player['grid'])}/{MAX_STACK_COUNT}", inline=True)
    if editable:
        embed.set_footer(text="Pick a building type below, then pick a stack to place it in.")
    return embed

class BetraySelectView(discord.ui.View):
    def __init__(self, guild_id, user_id):
        super().__init__(timeout=90)
        self.guild_id = guild_id
        self.user_id = user_id

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Choose an ally to betray", min_values=1, max_values=1)
    async def pick_target(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        target = select.values[0]
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.edit_message(content="You can't do that rn.", view=None)
            return

        alliances = prune_alliances(player)
        if str(target.id) not in alliances:
            await interaction.response.edit_message(
                content=f"You're not allied with {target.display_name}, so u can't betray them.",
                view=None
            )
            return

        break_alliance(self.guild_id, self.user_id, target.id)
        player["betrayer_until"] = time.time() + BETRAYAL_DURATION_SECONDS
        save_data(data)

        await interaction.response.edit_message(
            content=(
                f"You betrayed {target.display_name}! Alliance broken, you'll take "
                f"50% more troop losses whenever someone attacks you for the next 18 hours."
            ),
            view=None
        )

class AllianceProposalView(discord.ui.View):
    def __init__(self, guild_id, proposer_id, recipient_id, days):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.proposer_id = proposer_id
        self.recipient_id = recipient_id
        self.days = days

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.recipient_id:
            await interaction.response.send_message("This alliance offer isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        proposer_player = get_player(self.guild_id, self.proposer_id)
        recipient_player = get_player(self.guild_id, self.recipient_id)
        if not is_active_player(proposer_player) or not is_active_player(recipient_player):
            await interaction.response.edit_message(content="One of the players is no longer available.", view=None)
            return
        if clans_at_war(self.proposer_id, self.recipient_id):
            await interaction.response.edit_message(
                content="Your clans went to war before this could be accepted, alliance cancelled.", view=None
            )
            return
        form_alliance(self.guild_id, self.proposer_id, self.recipient_id, self.days)

        log_embed = discord.Embed(title="🤝 Alliance Formed", color=discord.Color.blue())
        log_embed.add_field(name="Player A", value=f"<@{self.proposer_id}>", inline=True)
        log_embed.add_field(name="Player B", value=f"<@{self.recipient_id}>", inline=True)
        log_embed.add_field(name="Duration", value=f"{self.days} days", inline=True)
        await send_action_log(self.guild_id, log_embed)

        await interaction.response.edit_message(
            content=f"<@{self.recipient_id}> accepted! <@{self.proposer_id}> and <@{self.recipient_id}> are now allied for {self.days} days.",
            view=None
        )

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=f"<@{self.recipient_id}> declined the alliance offer from <@{self.proposer_id}>.",
            view=None
        )

    @discord.ui.button(label="Counter-offer", style=discord.ButtonStyle.blurple)
    async def counter(self, interaction: discord.Interaction, button: discord.ui.Button):
        proposer_player = get_player(self.guild_id, self.proposer_id)
        recipient_player = get_player(self.guild_id, self.recipient_id)
        if not is_active_player(proposer_player) or not is_active_player(recipient_player):
            await interaction.response.edit_message(content="One of the players is no longer available.", view=None)
            return
        view = CounterDurationView(self.guild_id, original_proposer_id=self.proposer_id, countering_user_id=self.recipient_id)
        await interaction.response.edit_message(
            content=f"<@{self.recipient_id}>, choose your counter-offer duration:",
            view=view
        )


class CounterDurationView(discord.ui.View):
    def __init__(self, guild_id, original_proposer_id, countering_user_id):
        super().__init__(timeout=90)
        self.guild_id = guild_id
        self.original_proposer_id = original_proposer_id
        self.countering_user_id = countering_user_id

        for days in ALLIANCE_DURATION_OPTIONS_DAYS:
            btn = discord.ui.Button(label=f"{days} Days", style=discord.ButtonStyle.blurple)
            btn.callback = self._make_callback(days)
            self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.countering_user_id:
            await interaction.response.send_message("This isn't your counter-offer to make.", ephemeral=True)
            return False
        return True

    def _make_callback(self, days):
        async def callback(interaction: discord.Interaction):
            if clans_at_war(self.original_proposer_id, self.countering_user_id):
                await interaction.response.edit_message(
                    content="Your clans are at war, no alliances can be formed until peace is made.", view=None
                )
                return
            view = AllianceProposalView(self.guild_id, self.countering_user_id, self.original_proposer_id, days)
            await interaction.response.edit_message(
                content=(
                    f"<@{self.original_proposer_id}>, <@{self.countering_user_id}> countered with "
                    f"**{days} days**. Accept, decline, or counter again?"
                ),
                view=view
            )
        return callback


class AllianceDurationView(discord.ui.View):
    def __init__(self, guild_id, user_id, target_member):
        super().__init__(timeout=90)
        self.guild_id = guild_id
        self.user_id = user_id
        self.target_member = target_member

        for days in ALLIANCE_DURATION_OPTIONS_DAYS:
            btn = discord.ui.Button(label=f"{days} Days", style=discord.ButtonStyle.blurple)
            btn.callback = self._make_callback(days)
            self.add_item(btn)

    def _make_callback(self, days):
        async def callback(interaction: discord.Interaction):
            player = get_player(self.guild_id, self.user_id)
            target_player = get_player(self.guild_id, self.target_member.id)
            if not is_active_player(player) or not is_active_player(target_player):
                await interaction.response.edit_message(content="That player is no longer available.", view=None)
                return
            if clans_at_war(self.user_id, self.target_member.id):
                await interaction.response.edit_message(
                    content="Your clan is at war with theirs, no alliances can be formed until peace is made.",
                    view=None
                )
                return

            await interaction.response.edit_message(
                content=f"Alliance offer sent to {self.target_member.display_name}!",
                view=None
            )

            proposal_view = AllianceProposalView(self.guild_id, self.user_id, self.target_member.id, days)
            await interaction.channel.send(
                content=(
                    f"{self.target_member.mention}, <@{self.user_id}> is proposing an alliance "
                    f"for **{days} days**. Accept, decline, or counter?"
                ),
                view=proposal_view
            )
        return callback

class PickAllyView(discord.ui.View):
    def __init__(self, guild_id, user_id):
        super().__init__(timeout=90)
        self.guild_id = guild_id
        self.user_id = user_id

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Choose a player to ally up", min_values=1, max_values=1)
    async def pick_target(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        target = select.values[0]
        if target.id == self.user_id:
            await interaction.response.edit_message(content="You can't ally with yourself.", view=None)
            return
        target_player = get_player(self.guild_id, target.id)
        if not is_active_player(target_player):
            await interaction.response.edit_message(content=f"{target.display_name} isn't an active player.", view=None)
            return

        view = AllianceDurationView(self.guild_id, self.user_id, target)
        await interaction.response.edit_message(
            content=f"How long do you want to ally with {target.display_name}?",
            view=view
        )


class AllianceHubView(discord.ui.View):
    def __init__(self, guild_id, user_id, has_alliances):
        super().__init__(timeout=90)
        self.guild_id = guild_id
        self.user_id = user_id

        form_btn = discord.ui.Button(label="Form Alliance", style=discord.ButtonStyle.blurple, emoji="🤝")
        form_btn.callback = self.form_alliance_clicked
        self.add_item(form_btn)

        if has_alliances:
            betray_btn = discord.ui.Button(label="Betray", style=discord.ButtonStyle.danger, emoji="🗡")
            betray_btn.callback = self.betray_clicked
            self.add_item(betray_btn)

    async def form_alliance_clicked(self, interaction: discord.Interaction):
        view = PickAllyView(self.guild_id, self.user_id)
        await interaction.response.edit_message(content="Choose a player to ally with:", view=view)

    async def betray_clicked(self, interaction: discord.Interaction):
        view = BetraySelectView(self.guild_id, self.user_id)
        await interaction.response.edit_message(content="Choose an ally to betray:", view=view)
class GameHubView(discord.ui.View):
    def __init__(self, guild_id, user_id):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.user_id = user_id

        build_btn = discord.ui.Button(label="Build", style=discord.ButtonStyle.green, emoji="🏗")
        build_btn.callback = self.build
        self.add_item(build_btn)

        launch_btn = discord.ui.Button(label="Launch", style=discord.ButtonStyle.danger, emoji="🚀")
        launch_btn.callback = self.launch
        self.add_item(launch_btn)

        alliance_btn = discord.ui.Button(label="Alliance", style=discord.ButtonStyle.blurple, emoji="🤝")
        alliance_btn.callback = self.alliance
        self.add_item(alliance_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your hub lil bro.", ephemeral=True)
            return False
        return True

    async def build(self, interaction: discord.Interaction):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message("You can't do that rn.", ephemeral=True)
            return
        ensure_grid(player)
        save_data(data)
        embed = make_grid_embed(interaction.user, player, editable=True)
        view = GridBuildView(self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)

    async def launch(self, interaction: discord.Interaction):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message("You can't do that rn.", ephemeral=True)
            return
        ensure_grid(player)
        save_data(data)
        if player.get("silos", 0) <= 0:
            await interaction.response.send_message(
                "You need to build a Missile Silo first (Build → Build Silo).", ephemeral=True
            )
            return
        view = LaunchSiloPickView(self.guild_id, self.user_id)
        await interaction.response.send_message("🚀 Choose which silo to launch from:", view=view, ephemeral=True)

    async def alliance(self, interaction: discord.Interaction):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message("You can't do that rn.", ephemeral=True)
            return

        alliances = prune_alliances(player)
        save_data(data)
        has_alliances = len(alliances) > 0

        lines = ["**Your alliances:**"]
        if has_alliances:
            now = time.time()
            for uid, expiry in alliances.items():
                remaining = expiry - now
                days_left = int(remaining // 86400)
                hours_left = int((remaining % 86400) // 3600)
                ally_player = get_player(self.guild_id, uid)
                name = f"<@{uid}>"
                if ally_player and ally_player.get("eliminated"):
                    name += " (eliminated)"
                lines.append(f"• {name} expires in {days_left}d {hours_left}h")
        else:
            lines.append("You have no active alliances.")

        view = AllianceHubView(self.guild_id, self.user_id, has_alliances)
        await interaction.response.send_message("\n".join(lines), view=view, ephemeral=True)

class GridBuildView(discord.ui.View):
    def __init__(self, guild_id, user_id):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.user_id = user_id

        player = get_player(guild_id, user_id)
        if player:
            ensure_grid(player)
        city_price = get_city_price(player["cities"]) if player else 0
        port_price = get_port_price(player.get("ports", 0)) if player else 0
        stack_count = len(player["grid"]) if player else STARTING_STACK_COUNT
        stack_price = get_stack_price(stack_count)
        silo_price = get_silo_price(player.get("silos", 0)) if player else 0
        silo_slots_available = bool(player) and any(box.get("silo", 0) == 0 for box in player["grid"])
        pending_free_silos = player.get("pending_free_silos", 0) if player else 0
        pending_free_ports = player.get("pending_free_ports", 0) if player else 0
        free_silo_slots_available = pending_free_silos > 0 and any(box.get("silo", 0) == 0 for box in player["grid"])
        sam_upgrade_available = bool(player) and any(box.get("sam_level", 0) < SAM_MAX_LEVEL for box in player["grid"])
        sam_load_available = bool(player) and any(box.get("sam_level", 0) > 0 for box in player["grid"])

        city_btn = discord.ui.Button(label=f"Build City ({city_price:,} gold)", style=discord.ButtonStyle.green, emoji="🏙")
        city_btn.callback = self.pick_city_spot
        self.add_item(city_btn)

        port_btn = discord.ui.Button(label=f"Build Port ({port_price:,} gold)", style=discord.ButtonStyle.green, emoji="⚓")
        port_btn.callback = self.pick_port_spot
        self.add_item(port_btn)

        if silo_slots_available:
            silo_btn = discord.ui.Button(label=f"Build Silo ({silo_price:,} gold)", style=discord.ButtonStyle.blurple, emoji="🚀")
            silo_btn.callback = self.pick_silo_spot
            self.add_item(silo_btn)

        if free_silo_slots_available:
            free_silo_btn = discord.ui.Button(label=f"Place Free Silo ({pending_free_silos} pending)", style=discord.ButtonStyle.success, emoji="🎁")
            free_silo_btn.callback = self.pick_free_silo_spot
            self.add_item(free_silo_btn)

        if pending_free_ports > 0:
            free_port_btn = discord.ui.Button(label=f"Place Free Port ({pending_free_ports} pending)", style=discord.ButtonStyle.success, emoji="🎁")
            free_port_btn.callback = self.pick_free_port_spot
            self.add_item(free_port_btn)

        if sam_upgrade_available:
            sam_btn = discord.ui.Button(label="Build/Upgrade SAM", style=discord.ButtonStyle.blurple, emoji="🛡")
            sam_btn.callback = self.open_sam_upgrade
            self.add_item(sam_btn)

        if sam_load_available:
            load_sam_btn = discord.ui.Button(label=f"Load SAM ({SAM_INTERCEPTOR_PRICE:,} gold)", style=discord.ButtonStyle.blurple, emoji="🎯")
            load_sam_btn.callback = self.open_sam_load
            self.add_item(load_sam_btn)

        if stack_price is not None:
            stack_btn = discord.ui.Button(label=f"Buy Stack ({stack_price:,} gold)", style=discord.ButtonStyle.blurple, emoji="📦")
            stack_btn.callback = self.buy_stack
            self.add_item(stack_btn)

        back_btn = discord.ui.Button(label="Back to Hub", style=discord.ButtonStyle.grey)
        back_btn.callback = self.back_to_hub
        self.add_item(back_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your hub lil bro.", ephemeral=True)
            return False
        return True

    async def pick_city_spot(self, interaction: discord.Interaction):
        await self._open_position_picker(interaction, "cities", "city")

    async def pick_port_spot(self, interaction: discord.Interaction):
        await self._open_position_picker(interaction, "ports", "port")

    async def pick_silo_spot(self, interaction: discord.Interaction):
        await self._open_position_picker(interaction, "silo", "missile silo")

    async def pick_free_silo_spot(self, interaction: discord.Interaction):
        await self._open_position_picker(interaction, "free_silo", "free missile silo")

    async def pick_free_port_spot(self, interaction: discord.Interaction):
        await self._open_position_picker(interaction, "free_port", "free port")

    async def open_sam_upgrade(self, interaction: discord.Interaction):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message("You can't do that rn.", ephemeral=True)
            return
        ensure_grid(player)
        embed = make_grid_embed(interaction.user, player, editable=True)
        embed.description = "Pick a stack to build or upgrade its SAM."
        view = SamUpgradePickView(self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)

    async def open_sam_load(self, interaction: discord.Interaction):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message("You can't do that rn.", ephemeral=True)
            return
        ensure_grid(player)
        embed = make_grid_embed(interaction.user, player, editable=True)
        embed.description = "Pick a SAM to load an interceptor into."
        view = SamLoadPickView(self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)

    async def buy_stack(self, interaction: discord.Interaction):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message("You can't do that rn.", ephemeral=True)
            return
        ensure_grid(player)
        price = get_stack_price(len(player["grid"]))
        if price is None:
            await interaction.response.send_message(f"You're already at the max of {MAX_STACK_COUNT} stacks.", ephemeral=True)
            return
        if player["gold"] < price:
            await interaction.response.send_message(
                f"Not enough gold. You need {price:,}, you have {player['gold']:,}.", ephemeral=True
            )
            return
        player["gold"] -= price
        player["grid"].append({"cities": 0, "ports": 0, "silo": 0, "missile": None, "cooldown_until": 0, "sam_level": 0, "sam_stock": 0, "sam_cooldown_until": 0, "sam_shots_fired": 0})
        save_data(data)

        embed = make_grid_embed(interaction.user, player, editable=True)
        view = GridBuildView(self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _open_position_picker(self, interaction: discord.Interaction, structure_key, label):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message("You can't do that rn.", ephemeral=True)
            return
        ensure_grid(player)
        embed = make_grid_embed(interaction.user, player, editable=True)
        embed.description = f"Pick a stack to build your {label} in."
        view = BuildPositionView(self.guild_id, self.user_id, structure_key)
        await interaction.response.edit_message(embed=embed, view=view)

    async def back_to_hub(self, interaction: discord.Interaction):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.edit_message(content="You can't do that rn.", embed=None, view=None)
            return
        embed = make_hub_embed(interaction.user, player)
        view = GameHubView(self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)


class SamUpgradePickView(discord.ui.View):
    def __init__(self, guild_id, user_id):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.user_id = user_id

        player = get_player(guild_id, user_id)
        ensure_grid(player)

        for idx, box in enumerate(player["grid"]):
            level = box.get("sam_level", 0)
            if level >= SAM_MAX_LEVEL:
                continue
            price = SAM_PRICES[level]
            if level == 0:
                label = f"Stack {idx + 1}: Build Lvl 1 ({price:,}g)"
            else:
                label = f"Stack {idx + 1}: Lvl {level}→{level + 1} ({price:,}g)"
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.blurple)
            btn.callback = self._make_callback(idx)
            self.add_item(btn)

        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.grey)
        cancel_btn.callback = self.cancel
        self.add_item(cancel_btn)

    def _make_callback(self, idx):
        async def callback(interaction: discord.Interaction):
            await self.upgrade_at(interaction, idx)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your hub lil bro.", ephemeral=True)
            return False
        return True

    async def upgrade_at(self, interaction: discord.Interaction, idx):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message("You can't do that rn.", ephemeral=True)
            return
        ensure_grid(player)
        if idx >= len(player["grid"]):
            await interaction.response.send_message("That stack doesn't exist anymore.", ephemeral=True)
            return
        box = player["grid"][idx]
        level = box.get("sam_level", 0)
        if level >= SAM_MAX_LEVEL:
            await interaction.response.send_message("That SAM is already maxed out.", ephemeral=True)
            return
        price = SAM_PRICES[level]
        if player["gold"] < price:
            await interaction.response.send_message(
                f"Not enough gold. You need {price:,}, you have {player['gold']:,}.", ephemeral=True
            )
            return

        player["gold"] -= price
        box["sam_level"] = level + 1
        save_data(data)

        embed = make_grid_embed(interaction.user, player, editable=True)
        view = GridBuildView(self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)

    async def cancel(self, interaction: discord.Interaction):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.edit_message(content="You can't do that rn.", embed=None, view=None)
            return
        embed = make_grid_embed(interaction.user, player, editable=True)
        view = GridBuildView(self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)


class SamLoadPickView(discord.ui.View):
    def __init__(self, guild_id, user_id):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.user_id = user_id

        player = get_player(guild_id, user_id)
        ensure_grid(player)

        for idx, box in enumerate(player["grid"]):
            level = box.get("sam_level", 0)
            stock = box.get("sam_stock", 0)
            if level <= 0:
                continue
            btn = discord.ui.Button(label=f"Stack {idx + 1} ({stock}/{level} loaded)", style=discord.ButtonStyle.blurple)
            btn.callback = self._make_callback(idx)
            self.add_item(btn)

        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.grey)
        cancel_btn.callback = self.cancel
        self.add_item(cancel_btn)

    def _make_callback(self, idx):
        async def callback(interaction: discord.Interaction):
            await self.load_at(interaction, idx)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your hub lil bro.", ephemeral=True)
            return False
        return True

    async def load_at(self, interaction: discord.Interaction, idx):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message("You can't do that rn.", ephemeral=True)
            return
        ensure_grid(player)
        if idx >= len(player["grid"]):
            await interaction.response.send_message("That stack doesn't exist anymore.", ephemeral=True)
            return
        box = player["grid"][idx]
        level = box.get("sam_level", 0)
        stock = box.get("sam_stock", 0)
        if level <= 0:
            await interaction.response.send_message("That stack doesn't have a SAM.", ephemeral=True)
            return
        if player["gold"] < SAM_INTERCEPTOR_PRICE:
            await interaction.response.send_message(
                f"Not enough gold. You need {SAM_INTERCEPTOR_PRICE:,}, you have {player['gold']:,}.", ephemeral=True
            )
            return

        player["gold"] -= SAM_INTERCEPTOR_PRICE
        box["sam_stock"] = stock + 1
        save_data(data)

        embed = make_grid_embed(interaction.user, player, editable=True)
        view = GridBuildView(self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)

    async def cancel(self, interaction: discord.Interaction):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.edit_message(content="You can't do that rn.", embed=None, view=None)
            return
        embed = make_grid_embed(interaction.user, player, editable=True)
        view = GridBuildView(self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)


class BuildPositionView(discord.ui.View):
    def __init__(self, guild_id, user_id, structure_key):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.user_id = user_id
        self.structure_key = structure_key

        player = get_player(guild_id, user_id)
        ensure_grid(player)
        grid = player["grid"]

        if structure_key in ("silo", "free_silo"):
            eligible = [i for i, box in enumerate(grid) if box.get("silo", 0) == 0]
        else:
            eligible = list(range(len(grid)))

        for row_pos, idx in enumerate(eligible):
            btn = discord.ui.Button(label=str(idx + 1), style=discord.ButtonStyle.blurple, row=row_pos // 3)
            btn.callback = self._make_callback(idx)
            self.add_item(btn)

        cancel_row = (len(eligible) - 1) // 3 + 1 if eligible else 0
        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.grey, row=cancel_row)
        cancel_btn.callback = self.cancel
        self.add_item(cancel_btn)

    def _make_callback(self, idx):
        async def callback(interaction: discord.Interaction):
            await self.build_at(interaction, idx)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your hub lil bro.", ephemeral=True)
            return False
        return True

    async def build_at(self, interaction: discord.Interaction, idx):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message("You can't do that rn.", ephemeral=True)
            return
        ensure_grid(player)

        if self.structure_key in ("silo", "free_silo") and player["grid"][idx].get("silo", 0) > 0:
            await interaction.response.send_message("That stack already has a silo bro. Only one per stack.", ephemeral=True)
            return

        if self.structure_key == "free_silo":
            if player.get("pending_free_silos", 0) <= 0:
                await interaction.response.send_message("You don't have a free silo pending anymore.", ephemeral=True)
                return
            player["grid"][idx]["silo"] = 1
            player["silos"] = player.get("silos", 0) + 1
            player["pending_free_silos"] -= 1
        elif self.structure_key == "free_port":
            if player.get("pending_free_ports", 0) <= 0:
                await interaction.response.send_message("You don't have a free port pending anymore.", ephemeral=True)
                return
            player["grid"][idx]["ports"] += 1
            player["ports"] = player.get("ports", 0) + 1
            player["pending_free_ports"] -= 1
        else:
            if self.structure_key == "cities":
                price = get_city_price(player["cities"])
            elif self.structure_key == "ports":
                price = get_port_price(player.get("ports", 0))
            else:
                price = get_silo_price(player.get("silos", 0))

            if player["gold"] < price:
                await interaction.response.send_message(
                    f"Not enough gold. You need {price:,}, you have {player['gold']:,}.", ephemeral=True
                )
                return

            player["gold"] -= price
            if self.structure_key == "silo":
                player["grid"][idx]["silo"] = 1
                player["silos"] = player.get("silos", 0) + 1
            else:
                player["grid"][idx][self.structure_key] += 1
                player[self.structure_key] = player.get(self.structure_key, 0) + 1
                if self.structure_key == "cities":
                    player["troop_cap"] += CITY_TROOP_CAP_BONUS
        save_data(data)

        embed = make_grid_embed(interaction.user, player, editable=True)
        view = GridBuildView(self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)

    async def cancel(self, interaction: discord.Interaction):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.edit_message(content="You can't do that rn.", embed=None, view=None)
            return
        embed = make_grid_embed(interaction.user, player, editable=True)
        view = GridBuildView(self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)


class SmartBuildQuantityModal(discord.ui.Modal, title="How Many?"):
    amount_input = discord.ui.TextInput(label="Quantity", placeholder="e.g. 10")

    def __init__(self, guild_id, user_id, structure_key, stack_idx):
        super().__init__()
        self.guild_id = guild_id
        self.user_id = user_id
        self.structure_key = structure_key
        self.stack_idx = stack_idx

    async def on_submit(self, interaction: discord.Interaction):
        amount = await parse_amount(interaction, self.amount_input.value.strip())
        if amount is None:
            return

        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message("You can't do that rn.", ephemeral=True)
            return
        ensure_grid(player)

        price_fn = get_city_price if self.structure_key == "cities" else get_port_price
        built = 0
        spent = 0
        next_price = 0
        for _ in range(amount):
            next_price = price_fn(player[self.structure_key])
            if player["gold"] < next_price:
                break
            player["gold"] -= next_price
            player[self.structure_key] += 1
            player["grid"][self.stack_idx][self.structure_key] += 1
            if self.structure_key == "cities":
                player["troop_cap"] += CITY_TROOP_CAP_BONUS
            built += 1
            spent += next_price
        save_data(data)

        noun = "city" if self.structure_key == "cities" else "port"
        noun_plural = "cities" if self.structure_key == "cities" else "ports"

        if built == 0:
            await interaction.response.send_message(
                f"Couldn't afford a single {noun} ({next_price:,} gold, you have {player['gold']:,}).", ephemeral=True
            )
            return

        display = noun if built == 1 else noun_plural
        note = "" if built == amount else f" (asked for {amount}, only had gold for {built})"
        await interaction.response.send_message(
            f"✅ Built {built} {display} in Stack {self.stack_idx + 1} for {spent:,} gold total.{note}",
            ephemeral=True
        )


class SmartBuildStackPickView(discord.ui.View):
    def __init__(self, guild_id, user_id, structure_key):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.user_id = user_id
        self.structure_key = structure_key

        player = get_player(guild_id, user_id)
        ensure_grid(player)
        for idx in range(len(player["grid"])):
            btn = discord.ui.Button(label=f"Stack {idx + 1}", style=discord.ButtonStyle.blurple, row=idx // 5)
            btn.callback = self._make_callback(idx)
            self.add_item(btn)

    def _make_callback(self, idx):
        async def callback(interaction: discord.Interaction):
            await interaction.response.send_modal(SmartBuildQuantityModal(self.guild_id, self.user_id, self.structure_key, idx))
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your build menu lil bro.", ephemeral=True)
            return False
        return True


class SmartBuildView(discord.ui.View):
    def __init__(self, guild_id, user_id):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your build menu lil bro.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Build Cities", style=discord.ButtonStyle.success, emoji="🏙")
    async def build_cities(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SmartBuildStackPickView(self.guild_id, self.user_id, "cities")
        await interaction.response.edit_message(content="Pick a stack to bulk-build cities in:", view=view)

    @discord.ui.button(label="Build Ports", style=discord.ButtonStyle.success, emoji="⚓")
    async def build_ports(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SmartBuildStackPickView(self.guild_id, self.user_id, "ports")
        await interaction.response.edit_message(content="Pick a stack to bulk-build ports in:", view=view)

    @discord.ui.button(label="Auto-Refill SAMs", style=discord.ButtonStyle.primary, emoji="🛡")
    async def auto_refill_sams(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.edit_message(content="You can't do that rn.", view=None)
            return
        ensure_grid(player)

        total_loaded = 0
        total_cost = 0
        for box in player["grid"]:
            level = box.get("sam_level", 0)
            if level <= 0:
                continue
            need = level - box.get("sam_stock", 0)
            for _ in range(max(0, need)):
                if player["gold"] < SAM_INTERCEPTOR_PRICE:
                    break
                player["gold"] -= SAM_INTERCEPTOR_PRICE
                box["sam_stock"] = box.get("sam_stock", 0) + 1
                total_loaded += 1
                total_cost += SAM_INTERCEPTOR_PRICE
        save_data(data)

        if total_loaded == 0:
            await interaction.response.edit_message(content="Nothing to refill (SAMs are full, you have none, or you can't afford it).", view=None)
            return
        await interaction.response.edit_message(
            content=f"🛡 Refilled {total_loaded} SAM interceptor(s) across your stacks for {total_cost:,} gold total.",
            view=None
        )


class PublicGridView(discord.ui.View):
    def __init__(self, guild_id, member: discord.Member):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.member = member

    @discord.ui.button(label="Back to Hub", style=discord.ButtonStyle.grey)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = get_player(self.guild_id, self.member.id)
        if not is_active_player(player):
            await interaction.response.edit_message(content=f"{self.member.display_name} is no longer active.", embed=None, view=None)
            return
        embed = make_hub_embed(self.member, player)
        view = PublicHubView(self.guild_id, self.member)
        await interaction.response.edit_message(embed=embed, view=view)


class PublicAllianceView(discord.ui.View):
    def __init__(self, guild_id, member: discord.Member):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.member = member

    @discord.ui.button(label="Back to Hub", style=discord.ButtonStyle.grey)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = get_player(self.guild_id, self.member.id)
        if not is_active_player(player):
            await interaction.response.edit_message(content=f"{self.member.display_name} is no longer active.", embed=None, view=None)
            return
        embed = make_hub_embed(self.member, player)
        view = PublicHubView(self.guild_id, self.member)
        await interaction.response.edit_message(embed=embed, view=view)


class PublicHubView(discord.ui.View):
    def __init__(self, guild_id, member: discord.Member):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.member = member

    @discord.ui.button(label="View Stacks", style=discord.ButtonStyle.blurple, emoji="🗺")
    async def view_grid(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = get_player(self.guild_id, self.member.id)
        if not is_active_player(player):
            await interaction.response.edit_message(content=f"{self.member.display_name} is no longer active.", embed=None, view=None)
            return
        ensure_grid(player)
        save_data(data)
        embed = make_grid_embed(self.member, player, editable=False)
        view = PublicGridView(self.guild_id, self.member)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="View Alliances", style=discord.ButtonStyle.blurple, emoji="🤝")
    async def view_alliances(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = get_player(self.guild_id, self.member.id)
        if not is_active_player(player):
            await interaction.response.edit_message(content=f"{self.member.display_name} is no longer active.", embed=None, view=None)
            return
        prune_alliances(player)
        save_data(data)
        embed = make_alliance_embed(self.guild_id, self.member, player)
        view = PublicAllianceView(self.guild_id, self.member)
        await interaction.response.edit_message(embed=embed, view=view)


class LaunchSiloPickView(discord.ui.View):
    def __init__(self, guild_id, user_id):
        super().__init__(timeout=90)
        self.guild_id = guild_id
        self.user_id = user_id

        player = get_player(guild_id, user_id)
        ensure_grid(player)
        silo_indices = [i for i, box in enumerate(player["grid"]) if box.get("silo", 0) > 0]

        for idx in silo_indices:
            box = player["grid"][idx]
            if is_silo_on_cooldown(box):
                remaining = format_remaining(box["cooldown_until"] - time.time())
                btn = discord.ui.Button(label=f"Stack {idx + 1} (Cooldown {remaining})", style=discord.ButtonStyle.grey, emoji="🚀", disabled=True)
            else:
                status = "Loaded" if box.get("missile") else "Empty"
                btn = discord.ui.Button(label=f"Stack {idx + 1} ({status})", style=discord.ButtonStyle.blurple, emoji="🚀")
            btn.callback = self._make_callback(idx)
            self.add_item(btn)

        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.grey)
        cancel_btn.callback = self.cancel
        self.add_item(cancel_btn)

    def _make_callback(self, idx):
        async def callback(interaction: discord.Interaction):
            await self.pick_silo(interaction, idx)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your launch menu.", ephemeral=True)
            return False
        return True

    async def pick_silo(self, interaction: discord.Interaction, idx):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.edit_message(content="You can't do that rn.", view=None)
            return
        ensure_grid(player)
        if idx >= len(player["grid"]) or player["grid"][idx].get("silo", 0) <= 0:
            await interaction.response.edit_message(content="That silo isn't there anymore.", view=None)
            return
        if is_silo_on_cooldown(player["grid"][idx]):
            remaining = format_remaining(player["grid"][idx]["cooldown_until"] - time.time())
            await interaction.response.edit_message(content=f"That silo is on cooldown for {remaining}. Use a different one.", view=None)
            return

        loaded_missile = player["grid"][idx].get("missile")
        if loaded_missile:
            missile_label = MISSILE_TYPES[loaded_missile]["label"]
            view = LaunchTargetSelectView(self.guild_id, self.user_id, idx, loaded_missile)
            await interaction.response.edit_message(
                content=f"Stack {idx + 1} has a {missile_label} loaded and ready. Who are you launching at?",
                view=view
            )
            return

        view = LaunchMissilePickView(self.guild_id, self.user_id, idx)
        await interaction.response.edit_message(
            content=f"Stack {idx + 1}'s silo is empty. Choose a missile to load:",
            view=view
        )

    async def cancel(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Launch cancelled.", view=None)


class LaunchMissilePickView(discord.ui.View):
    def __init__(self, guild_id, user_id, silo_index):
        super().__init__(timeout=90)
        self.guild_id = guild_id
        self.user_id = user_id
        self.silo_index = silo_index

        for key, info in MISSILE_TYPES.items():
            btn = discord.ui.Button(label=f"{info['label']} ({info['price']:,} gold)", style=discord.ButtonStyle.danger, emoji=info["emoji"])
            btn.callback = self._make_callback(key)
            self.add_item(btn)

        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.grey)
        cancel_btn.callback = self.cancel
        self.add_item(cancel_btn)

    def _make_callback(self, missile_key):
        async def callback(interaction: discord.Interaction):
            await self.pick_missile(interaction, missile_key)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your launch menu.", ephemeral=True)
            return False
        return True

    async def pick_missile(self, interaction: discord.Interaction, missile_key):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.edit_message(content="You can't do that rn.", view=None)
            return
        ensure_grid(player)
        if self.silo_index >= len(player["grid"]) or player["grid"][self.silo_index].get("silo", 0) <= 0:
            await interaction.response.edit_message(content="That silo isn't there anymore.", view=None)
            return
        if is_silo_on_cooldown(player["grid"][self.silo_index]):
            remaining = format_remaining(player["grid"][self.silo_index]["cooldown_until"] - time.time())
            await interaction.response.edit_message(content=f"That silo is on cooldown for {remaining}. Use a different one.", view=None)
            return
        if player["grid"][self.silo_index].get("missile"):
            await interaction.response.edit_message(content="That silo already has a missile loaded.", view=None)
            return

        price = MISSILE_TYPES[missile_key]["price"]
        if player["gold"] < price:
            await interaction.response.edit_message(
                content=f"Not enough gold. You need {price:,}, you have {player['gold']:,}.", view=None
            )
            return

        player["gold"] -= price
        player["grid"][self.silo_index]["missile"] = missile_key
        save_data(data)

        view = LaunchTargetSelectView(self.guild_id, self.user_id, self.silo_index, missile_key)
        missile_label = MISSILE_TYPES[missile_key]["label"]
        await interaction.response.edit_message(
            content=f"Loaded {missile_label} into Stack {self.silo_index + 1}. Who are you launching at?",
            view=view
        )

    async def cancel(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Launch cancelled.", view=None)


class LaunchTargetSelectView(discord.ui.View):
    def __init__(self, guild_id, user_id, silo_index, missile_key):
        super().__init__(timeout=90)
        self.guild_id = guild_id
        self.user_id = user_id
        self.silo_index = silo_index
        self.missile_key = missile_key

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your launch menu.", ephemeral=True)
            return False
        return True

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Choose who to launch at", min_values=1, max_values=1)
    async def pick_target(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        target = select.values[0]
        attacker = get_player(self.guild_id, self.user_id)
        if not is_active_player(attacker):
            await interaction.response.edit_message(content="You can't do that rn.", view=None)
            return
        if target.id == self.user_id:
            await interaction.response.edit_message(content="You can't nuke yourself gng.", view=None)
            return
        defender = get_player(self.guild_id, target.id)
        if not is_active_player(defender):
            await interaction.response.edit_message(content=f"{target.display_name} isn't a valid target.", view=None)
            return
        if is_immune(defender):
            remaining = format_remaining(defender["immune_until"] - time.time())
            await interaction.response.edit_message(
                content=f"{target.display_name} still has spawn immunity for {remaining}. You can't bomb them yet.", view=None
            )
            return
        ensure_grid(attacker)
        if self.silo_index >= len(attacker["grid"]) or attacker["grid"][self.silo_index].get("silo", 0) <= 0:
            await interaction.response.edit_message(content="That silo isn't there anymore.", view=None)
            return

        alliances = prune_alliances(attacker)
        save_data(data)
        if str(target.id) in alliances:
            view = LaunchBetrayConfirmView(self.guild_id, interaction.user, target, self.silo_index, self.missile_key)
            await interaction.response.edit_message(
                content=(
                    f"You're currently **allied** with {target.display_name}. Launching a missile at them will break "
                    f"the alliance and mark you as a betrayer (you'll take extra damage while defending)."
                ),
                view=view
            )
            return

        ensure_grid(defender)
        view = LaunchStackPickView(self.guild_id, interaction.user, target, self.silo_index, self.missile_key)
        await interaction.response.edit_message(
            content=f"Which of {target.display_name}'s stacks are you targeting?",
            view=view
        )


class LaunchBetrayConfirmView(discord.ui.View):
    def __init__(self, guild_id, attacker_member, defender_member, silo_index, missile_key):
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.attacker_member = attacker_member
        self.defender_member = defender_member
        self.silo_index = silo_index
        self.missile_key = missile_key

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.attacker_member.id:
            await interaction.response.send_message("This isn't your confirmation menu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Betray & Launch", style=discord.ButtonStyle.danger, emoji="🗡")
    async def betray_and_launch(self, interaction: discord.Interaction, button: discord.ui.Button):
        attacker = get_player(self.guild_id, self.attacker_member.id)
        defender = get_player(self.guild_id, self.defender_member.id)
        if not is_active_player(attacker) or not is_active_player(defender):
            await interaction.response.edit_message(content="One of the players is no longer available.", view=None)
            return

        break_alliance(self.guild_id, self.attacker_member.id, self.defender_member.id)
        attacker["betrayer_until"] = time.time() + BETRAYAL_DURATION_SECONDS
        save_data(data)

        ensure_grid(defender)
        view = LaunchStackPickView(self.guild_id, self.attacker_member, self.defender_member, self.silo_index, self.missile_key)
        await interaction.response.edit_message(
            content=(
                f"You betrayed your alliance with {self.defender_member.display_name}! "
                f"Which of their stacks are you targeting?"
            ),
            view=view
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Launch cancelled.", view=None)


class LaunchStackPickView(discord.ui.View):
    def __init__(self, guild_id, attacker_member, defender_member, silo_index, missile_key):
        super().__init__(timeout=90)
        self.guild_id = guild_id
        self.attacker_member = attacker_member
        self.defender_member = defender_member
        self.silo_index = silo_index
        self.missile_key = missile_key

        defender = get_player(guild_id, defender_member.id)
        ensure_grid(defender)
        stack_count = len(defender["grid"])

        for idx in range(stack_count):
            btn = discord.ui.Button(label=f"Stack {idx + 1}", style=discord.ButtonStyle.danger, row=idx // 3)
            btn.callback = self._make_callback(idx)
            self.add_item(btn)

        cancel_row = (stack_count - 1) // 3 + 1
        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.grey, row=cancel_row)
        cancel_btn.callback = self.cancel
        self.add_item(cancel_btn)

    def _make_callback(self, idx):
        async def callback(interaction: discord.Interaction):
            await self.launch_at(interaction, idx)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.attacker_member.id:
            await interaction.response.send_message("This isn't your launch menu.", ephemeral=True)
            return False
        return True

    async def launch_at(self, interaction: discord.Interaction, idx):
        attacker = get_player(self.guild_id, self.attacker_member.id)
        defender = get_player(self.guild_id, self.defender_member.id)
        if not is_active_player(attacker):
            await interaction.response.edit_message(content="You can't do that rn.", view=None)
            return
        if not is_active_player(defender):
            await interaction.response.edit_message(content="That player is no longer a valid target.", view=None)
            return
        ensure_grid(attacker)
        ensure_grid(defender)

        if self.silo_index >= len(attacker["grid"]) or attacker["grid"][self.silo_index].get("silo", 0) <= 0:
            await interaction.response.edit_message(content="That silo isn't there anymore.", view=None)
            return
        if attacker["grid"][self.silo_index].get("missile") != self.missile_key:
            await interaction.response.edit_message(content="That missile isn't loaded anymore.", view=None)
            return
        if is_silo_on_cooldown(attacker["grid"][self.silo_index]):
            remaining = format_remaining(attacker["grid"][self.silo_index]["cooldown_until"] - time.time())
            await interaction.response.edit_message(content=f"That silo is on cooldown for {remaining}. Use a different one.", view=None)
            return
        if idx >= len(defender["grid"]):
            await interaction.response.edit_message(content="That stack doesn't exist anymore.", view=None)
            return

        attacker["grid"][self.silo_index]["missile"] = None
        attacker["grid"][self.silo_index]["cooldown_until"] = time.time() + SILO_COOLDOWN_SECONDS
        attacker["immune_until"] = 0

        intercepted = try_intercept(defender, idx)

        if intercepted:
            cities_destroyed = ports_destroyed = silos_destroyed = troops_destroyed = 0
            total_destroyed = 0
            eliminated = False
            gold_captured = 0
            war_result = None
        else:
            cities_destroyed, ports_destroyed, silos_destroyed, troops_destroyed = resolve_missile_strike(defender, idx, self.missile_key)
            total_destroyed = cities_destroyed + ports_destroyed + silos_destroyed

            eliminated = False
            gold_captured = 0
            if defender["troops"] <= 0 and defender["cities"] <= 0 and defender["ports"] <= 0:
                eliminated = True
                defender["eliminated"] = True
                defender["eliminated_at"] = time.time()
                defender["alliances"] = {}
                gold_captured = defender["gold"]
                attacker["gold"] += gold_captured
                defender["gold"] = 0

            war_result = award_war_points(self.attacker_member.id, self.defender_member.id, CLAN_WAR_POINTS_PER_MISSILE_HIT, eliminated)

        save_data(data)

        missile_label = MISSILE_TYPES[self.missile_key]["label"]
        missile_emoji = MISSILE_TYPES[self.missile_key]["emoji"]
        guild_obj = bot.get_guild(int(self.guild_id))
        guild_name = guild_obj.name if guild_obj else "Unknown Server"

        dm_sent = True
        try:
            if intercepted:
                dm_embed = discord.Embed(
                    title="🛡 SAM Intercept!",
                    description=f"Your Stack {idx + 1}'s SAM shot down a {missile_label} launched by **{self.attacker_member.display_name}**!",
                    color=discord.Color.green()
                )
                dm_embed.add_field(name="🎯 Stack Defended", value=f"Stack {idx + 1}", inline=True)
                dm_embed.add_field(name="📍 Server", value=guild_name, inline=False)
                dm_embed.set_footer(text="that SAM needs a reload and a cooldown before it can do that again")
            else:
                dm_embed = discord.Embed(
                    title=f"{missile_emoji} You've Been Bombed!",
                    description=f"**{self.attacker_member.display_name}** launched a {missile_label} at your Stack {idx + 1}.",
                    color=discord.Color.dark_red()
                )
                dm_embed.add_field(name=f"{missile_emoji} Missile Used", value=missile_label, inline=True)
                dm_embed.add_field(name="🎯 Stack Hit", value=f"Stack {idx + 1}", inline=True)
                dm_embed.add_field(name="🏙 Structures Lost", value=f"{cities_destroyed} cities, {ports_destroyed} ports, {silos_destroyed} silos", inline=True)
                if troops_destroyed > 0:
                    dm_embed.add_field(name="💀 Troops Lost", value=f"{troops_destroyed:,}", inline=True)
                if eliminated:
                    dm_embed.add_field(name="💀 Status", value="You have been eliminated and can no longer rejoin.", inline=False)
                    if gold_captured > 0:
                        dm_embed.add_field(name="💰 Gold Looted", value=f"{gold_captured:,}", inline=True)
                dm_embed.add_field(name="📍 Server", value=guild_name, inline=False)
                dm_embed.set_footer(text="do /attack or build another silo to retaliate")
            await self.defender_member.send(embed=dm_embed)
        except (discord.Forbidden, discord.HTTPException):
            dm_sent = False

        if intercepted:
            result_lines = [
                f"Your {missile_label} from Stack {self.silo_index + 1} was intercepted by {self.defender_member.display_name}'s SAM defense on Stack {idx + 1}!",
                "No structures were destroyed."
            ]
        else:
            result_lines = [
                f"You launched a {missile_label} from Stack {self.silo_index + 1} at {self.defender_member.display_name}'s Stack {idx + 1}.",
                f"Destroyed {total_destroyed} structure(s) ({cities_destroyed} cities, {ports_destroyed} ports, {silos_destroyed} silos)."
            ]
            if troops_destroyed > 0:
                result_lines.append(f"Also wiped out {troops_destroyed:,} of their troops.")
        if not dm_sent:
            result_lines.append("⚠️ Couldn't DM them (their settings are blocking it) they won't know unless you tell them.")
        if eliminated:
            result_lines.append(f"{self.defender_member.display_name} has been eliminated!")
            if gold_captured > 0:
                result_lines.append(f"You looted {gold_captured:,} gold from their fallen nation!")
        if war_result:
            result_lines.append(
                f"⚔️ +{war_result['gained']} war points for [{war_result['clan']}] vs [{war_result['enemy']}] "
                f"(now {war_result['total']}/{CLAN_WAR_POINTS_TO_WIN})."
            )
            if war_result["won"]:
                result_lines.append(
                    f"🏆 [{war_result['clan']}] hit the war goal and defeated [{war_result['enemy']}]! "
                    f"Every member got {CLAN_WAR_REWARD_GOLD:,} gold."
                )

        log_embed = discord.Embed(title=f"{missile_emoji} Missile Launch", color=discord.Color.green() if intercepted else discord.Color.dark_red())
        log_embed.add_field(name="Launcher", value=self.attacker_member.mention, inline=True)
        log_embed.add_field(name="Target", value=self.defender_member.mention, inline=True)
        log_embed.add_field(name="Missile", value=missile_label, inline=True)
        log_embed.add_field(name="Stack Hit", value=f"Stack {idx + 1}", inline=True)
        if intercepted:
            log_embed.add_field(name="🛡 Intercepted", value="Yes", inline=True)
        else:
            log_embed.add_field(name="Structures Destroyed", value=f"{cities_destroyed} cities, {ports_destroyed} ports, {silos_destroyed} silos", inline=True)
            if troops_destroyed > 0:
                log_embed.add_field(name="Troops Destroyed", value=f"{troops_destroyed:,}", inline=True)
        if eliminated:
            log_embed.add_field(name="💀 Eliminated", value=f"{self.defender_member.mention} (looted {gold_captured:,} gold)", inline=False)
        await send_action_log(self.guild_id, log_embed)

        await interaction.response.edit_message(content="\n".join(result_lines), view=None)

    async def cancel(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Launch cancelled.", view=None)


@bot.tree.command(name="gamehub", description="Manage your lands, or optionally view another player's stuff")
@discord.app_commands.describe(member="Optional: view this player's hub instead of your own (read only)")
async def gamehub(interaction: discord.Interaction, member: discord.Member = None):
    if member is not None:
        target_player = get_player(interaction.guild_id, member.id)
        if not is_active_player(target_player):
            await interaction.response.send_message(
                f"{member.display_name} hasn't joined the game or has been eliminated.", ephemeral=True
            )
            return
        embed = make_hub_embed(member, target_player)
        view = PublicHubView(interaction.guild_id, member)
        await interaction.response.send_message(embed=embed, view=view)
        return

    player = get_player(interaction.guild_id, interaction.user.id)
    if not is_active_player(player):
        await interaction.response.send_message(
            "You haven't joined the game yet (or you've been eliminated). Use /joingame first.", ephemeral=True
        )
        return
    embed = make_hub_embed(interaction.user, player)
    view = GameHubView(interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="smartbuild", description="Bulk-build cities/ports in one go, or auto-refill all your SAMs")
async def smartbuild(interaction: discord.Interaction):
    player = get_player(interaction.guild_id, interaction.user.id)
    if not is_active_player(player):
        await interaction.response.send_message(
            "You haven't joined the game yet (or you've been eliminated). Use /joingame first.", ephemeral=True
        )
        return
    view = SmartBuildView(interaction.guild_id, interaction.user.id)
    await interaction.response.send_message("What do you want to bulk-build?", view=view, ephemeral=True)


def make_streak_embed(member, player):
    streak_number = player.get("streak_number", 0)
    claimed_days = set(player.get("streak_claimed_days", []))
    embed = discord.Embed(title=f"{member.display_name}'s Daily Streak", color=discord.Color.gold())
    embed.add_field(name="Current Streak", value=f"{streak_number} day{'s' if streak_number != 1 else ''}", inline=False)
    for day in range(1, 8):
        if day > streak_number:
            status = "🔒 Locked"
        elif day in claimed_days:
            status = "✅ Claimed"
        else:
            status = "🎁 Ready to claim!"
        embed.add_field(name=f"Day {day}", value=f"{get_streak_reward_label(day)}\n{status}", inline=True)
    bonus_available = max(0, streak_number - 7) - player.get("streak_bonus_claimed", 0)
    if streak_number > 7:
        bonus_status = f"🎁 {bonus_available} pending!" if bonus_available > 0 else "✅ Up to date"
    else:
        bonus_status = "🔒 Locked bruh"
    embed.add_field(name="Day 7+", value=f"{STREAK_BONUS_GOLD:,} gold each\n{bonus_status}", inline=True)
    embed.set_footer(text="Do at least one command each day (by 10 PM UTC) to keep your streak going.")
    return embed


class StreakRewardsView(discord.ui.View):
    def __init__(self, guild_id, user_id):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.user_id = user_id

        player = get_player(guild_id, user_id)
        streak_number = player.get("streak_number", 0) if player else 0
        claimed_days = set(player.get("streak_claimed_days", [])) if player else set()

        for day in range(1, 8):
            if day <= streak_number and day not in claimed_days:
                btn = discord.ui.Button(label=f"Claim Day {day}", style=discord.ButtonStyle.green, row=(day - 1) // 4)
                btn.callback = self._make_callback(day)
                self.add_item(btn)

        bonus_available = max(0, streak_number - 7) - (player.get("streak_bonus_claimed", 0) if player else 0)
        if bonus_available > 0:
            bonus_btn = discord.ui.Button(label=f"Claim Day 7+ ({bonus_available} pending)", style=discord.ButtonStyle.green, row=2)
            bonus_btn.callback = self.claim_bonus
            self.add_item(bonus_btn)

    def _make_callback(self, day):
        async def callback(interaction: discord.Interaction):
            await self.claim_day(interaction, day)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your streak menu.", ephemeral=True)
            return False
        return True

    async def claim_day(self, interaction: discord.Interaction, day):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message("You can't do that rn.", ephemeral=True)
            return
        claimed_days = player.setdefault("streak_claimed_days", [])
        if day > player.get("streak_number", 0):
            await interaction.response.send_message("You haven't unlocked that day yet.", ephemeral=True)
            return
        if day in claimed_days:
            await interaction.response.send_message("You already claimed that day.", ephemeral=True)
            return

        apply_streak_reward(player, day)
        claimed_days.append(day)
        save_data(data)

        embed = make_streak_embed(interaction.user, player)
        view = StreakRewardsView(self.guild_id, self.user_id)
        await interaction.response.edit_message(
            content=f"Claimed Day {day}: {get_streak_reward_label(day)}!",
            embed=embed,
            view=view
        )

    async def claim_bonus(self, interaction: discord.Interaction):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message("You can't do that rn.", ephemeral=True)
            return
        available = max(0, player.get("streak_number", 0) - 7) - player.get("streak_bonus_claimed", 0)
        if available <= 0:
            await interaction.response.send_message("Nothing pending to claim.", ephemeral=True)
            return

        apply_streak_reward(player, 8)
        player["streak_bonus_claimed"] = player.get("streak_bonus_claimed", 0) + 1
        save_data(data)

        embed = make_streak_embed(interaction.user, player)
        view = StreakRewardsView(self.guild_id, self.user_id)
        await interaction.response.edit_message(
            content=f"Claimed a Day 7+ bonus: {STREAK_BONUS_GOLD:,} gold!",
            embed=embed,
            view=view
        )


@bot.tree.command(name="streakrewards", description="View and claim your daily streak rewards")
async def streakrewards(interaction: discord.Interaction):
    player = get_player(interaction.guild_id, interaction.user.id)
    if not is_active_player(player):
        await interaction.response.send_message("You need to /joingame first.", ephemeral=True)
        return
    embed = make_streak_embed(interaction.user, player)
    view = StreakRewardsView(interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="leaderboard", description="See the leaderboard")
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()   

    players = get_guild_players(interaction.guild_id)
    if not players:
        await interaction.followup.send("No one has joined the game yet. rip", ephemeral=True)
        return

    sorted_players = sorted(players.items(), key=lambda item: item[1]["troops"], reverse=True)

    lines = []
    medals = ["🏆", "🥈", "🥉"]
    for i, (user_id, pdata) in enumerate(sorted_players):
        rank_icon = medals[i] if i < 3 else f"#{i+1}"
        member = interaction.guild.get_member(int(user_id))
        if member is None:
            try:
                member = await interaction.guild.fetch_member(int(user_id))
            except discord.NotFound:
                member = None
        name = member.display_name if member else f"User {user_id}"
        clan_tag, _ = find_clan_for_user(user_id)
        if clan_tag:
            name += f" [{clan_tag}]"
        if pdata.get("eliminated"):
            name += " (eliminated)"
        lines.append(f"{rank_icon} {name} — Troops: {pdata['troops']:,}/{pdata['troop_cap']:,} | Gold: {pdata['gold']:,}")

    embed = discord.Embed(title="da Leaderboard", description="\n".join(lines), color=discord.Color.blue())
    await interaction.followup.send(embed=embed)


class AttackView(discord.ui.View):
    def __init__(self, guild_id, attacker_member, defender_member, attacker_troops):
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.attacker_member = attacker_member
        self.defender_member = defender_member

        for pct in ATTACK_PERCENT_OPTIONS:
            amount = round(attacker_troops * pct)
            label = f"{ATTACK_PERCENT_LABELS[pct]} ({amount:,})"
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.red)
            btn.callback = self._make_callback(pct)
            self.add_item(btn)

    def _make_callback(self, pct):
        async def callback(interaction: discord.Interaction):
            await self.resolve_attack(interaction, pct)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.attacker_member.id:
            await interaction.response.send_message("This isn't your attack menu.", ephemeral=True)
            return False
        return True

    async def resolve_attack(self, interaction: discord.Interaction, percent):
        attacker = get_player(self.guild_id, self.attacker_member.id)
        defender = get_player(self.guild_id, self.defender_member.id)

        if not is_active_player(attacker):
            await interaction.response.edit_message(content="You can't attack right now.", embed=None, view=None)
            return
        if not is_active_player(defender):
            await interaction.response.edit_message(content="That player is no longer a valid target.", embed=None, view=None)
            return

        attack_troops = round(attacker["troops"] * percent)
        if attack_troops <= 0:
            await interaction.response.edit_message(content="You don't have enough troops to send.", embed=None, view=None)
            return

        attacker["immune_until"] = 0

        defender_troops_before = defender["troops"]

        defender_loss_rate = random.uniform(DEFENDER_LOSS_MIN, DEFENDER_LOSS_MAX)
        if is_betrayer(defender):
            defender_loss_rate *= BETRAYER_LOSS_MULTIPLIER
        if is_attack_buffed(attacker):
            defender_loss_rate *= ATTACK_BUFF_DAMAGE_MULTIPLIER
        troop_loss = round(attack_troops * defender_loss_rate)
        defender["troops"] = max(0, defender["troops"] - troop_loss)

        attacker_loss_rate = random.uniform(ATTACKER_LOSS_MIN, ATTACKER_LOSS_MAX)
        attacker_loss = round(attack_troops * attacker_loss_rate)
        attacker["troops"] = max(0, attacker["troops"] - attacker_loss)

        defender_id = str(self.defender_member.id)
        streaks = attacker.setdefault("zero_troop_streaks", {})
        cities_captured = 0
        ports_captured = 0

        if defender_troops_before <= 0:
            streak = streaks.get(defender_id, 0)
            capture_cap = ZERO_TROOP_CAPTURE_BASE + streak
            for _ in range(capture_cap):
                pool = (["cities"] * defender["cities"]) + (["ports"] * defender["ports"])
                if not pool:
                    break
                if random.random() < HIGH_CAPTURE_CHANCE:
                    structure_key = random.choice(pool)
                    defender[structure_key] -= 1
                    attacker[structure_key] += 1
                    transfer_structure_box(attacker, defender, structure_key)
                    if structure_key == "cities":
                        cities_captured += 1
                        defender["troop_cap"] = max(STARTING_TROOP_CAP, defender["troop_cap"] - CITY_TROOP_CAP_BONUS)
                        attacker["troop_cap"] += CITY_TROOP_CAP_BONUS
                    else:
                        ports_captured += 1
            streaks[defender_id] = streak + 1
        else:
            streaks[defender_id] = 0
            capture_chance = HIGH_CAPTURE_CHANCE if defender_troops_before < attack_troops else BASE_CAPTURE_CHANCE
            if defender["cities"] > 0 and random.random() < capture_chance:
                cities_captured = 1
                defender["cities"] -= 1
                defender["troop_cap"] = max(STARTING_TROOP_CAP, defender["troop_cap"] - CITY_TROOP_CAP_BONUS)
                attacker["cities"] += 1
                attacker["troop_cap"] += CITY_TROOP_CAP_BONUS
                transfer_structure_box(attacker, defender, "cities")
            if defender["ports"] > 0 and random.random() < capture_chance:
                ports_captured = 1
                defender["ports"] -= 1
                attacker["ports"] += 1
                transfer_structure_box(attacker, defender, "ports")

        defender["troops"] = min(defender["troops"], defender["troop_cap"])
        captured_city = cities_captured > 0 or ports_captured > 0

        eliminated = False
        gold_captured = 0
        if defender["troops"] <= 0 and defender["cities"] <= 0 and defender["ports"] <= 0:
            eliminated = True
            defender["eliminated"] = True
            defender["eliminated_at"] = time.time()
            defender["alliances"] = {}
            gold_captured = defender["gold"]
            attacker["gold"] += gold_captured
            defender["gold"] = 0

        war_result = award_war_points(self.attacker_member.id, self.defender_member.id, CLAN_WAR_POINTS_PER_ATTACK, eliminated)

        save_data(data)

        guild_obj = bot.get_guild(int(self.guild_id))
        guild_name = guild_obj.name if guild_obj else "Unknown Server"

        dm_sent = True
        try:
            dm_embed = discord.Embed(
                title="⚔️Ur Under Attack!⚔️",
                description=f"**{self.attacker_member.display_name}** launched an invasion of your lands, Beat em up",
                color=discord.Color.red()
            )
            dm_embed.add_field(name="🪖 Troops Sent Against You", value=f"{attack_troops:,}", inline=True)
            dm_embed.add_field(name="💥 Troops You Lost", value=f"{troop_loss:,}", inline=True)
            dm_embed.add_field(name="🏙 Structures Lost", value=f"{cities_captured} cities, {ports_captured} ports", inline=True)
            if eliminated:
                dm_embed.add_field(name="💀 Status", value="You have been eliminated and can no longer rejoin.", inline=False)
                if gold_captured > 0:
                    dm_embed.add_field(name="💰 Gold Looted", value=f"{gold_captured:,}", inline=True)
            dm_embed.add_field(name="📍 Server", value=guild_name, inline=False)
            dm_embed.set_footer(text="do /attack to retaliate")
            await self.defender_member.send(embed=dm_embed)
        except (discord.Forbidden, discord.HTTPException):
            dm_sent = False

        result_lines = [
            f"You attacked {self.defender_member.display_name} with {attack_troops:,} troops.",
            f"They lost {troop_loss:,} troops.",
            f"You lost {attacker_loss:,} troops from the assault."
        ]
        if not dm_sent:
            result_lines.append("⚠️ Couldn't DM them (their settings are blocking it) they won't know unless you tell them hehe.")
        if captured_city:
            captured_bits = []
            if cities_captured > 0:
                captured_bits.append(f"{cities_captured} of their cities" if cities_captured > 1 else "one of their cities")
            if ports_captured > 0:
                captured_bits.append(f"{ports_captured} of their ports" if ports_captured > 1 else "one of their ports")
            result_lines.append(f"You captured {' and '.join(captured_bits)} W attack gng")
        if eliminated:
            result_lines.append(f"{self.defender_member.display_name} has been eliminated!")
            if gold_captured > 0:
                result_lines.append(f"You looted {gold_captured:,} gold from their fallen nation!")
        if war_result:
            result_lines.append(
                f"⚔️ +{war_result['gained']} war points for [{war_result['clan']}] vs [{war_result['enemy']}] "
                f"(now {war_result['total']}/{CLAN_WAR_POINTS_TO_WIN})."
            )
            if war_result["won"]:
                result_lines.append(
                    f"🏆 [{war_result['clan']}] hit the war goal and defeated [{war_result['enemy']}]! "
                    f"Every member got {CLAN_WAR_REWARD_GOLD:,} gold."
                )

        log_embed = discord.Embed(title="⚔️ Attack", color=discord.Color.orange())
        log_embed.add_field(name="Attacker", value=self.attacker_member.mention, inline=True)
        log_embed.add_field(name="Defender", value=self.defender_member.mention, inline=True)
        log_embed.add_field(name="Troops Sent", value=f"{attack_troops:,}", inline=True)
        log_embed.add_field(name="Troops Lost (Defender)", value=f"{troop_loss:,}", inline=True)
        log_embed.add_field(name="Troops Lost (Attacker)", value=f"{attacker_loss:,}", inline=True)
        if captured_city:
            log_embed.add_field(name="Structures Captured", value=f"{cities_captured} cities, {ports_captured} ports", inline=True)
        if eliminated:
            log_embed.add_field(name="💀 Eliminated", value=f"{self.defender_member.mention} (looted {gold_captured:,} gold)", inline=False)
        await send_action_log(self.guild_id, log_embed)

        await interaction.response.edit_message(content="\n".join(result_lines), embed=None, view=None)
class BetrayConfirmView(discord.ui.View):
    def __init__(self, guild_id, attacker_member, defender_member):
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.attacker_member = attacker_member
        self.defender_member = defender_member

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.attacker_member.id:
            await interaction.response.send_message("This isn't your confirmation menu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Betray & Attack", style=discord.ButtonStyle.danger, emoji="🗡")
    async def betray_and_attack(self, interaction: discord.Interaction, button: discord.ui.Button):
        attacker = get_player(self.guild_id, self.attacker_member.id)
        defender = get_player(self.guild_id, self.defender_member.id)
        if not is_active_player(attacker) or not is_active_player(defender):
            await interaction.response.edit_message(content="One of the players is no longer available.", embed=None, view=None)
            return

        break_alliance(self.guild_id, self.attacker_member.id, self.defender_member.id)
        attacker["betrayer_until"] = time.time() + BETRAYAL_DURATION_SECONDS
        save_data(data)

        embed = discord.Embed(
            title=f"Attack {self.defender_member.display_name}?",
            description="Choose how many troops to commit to this attack.",
            color=discord.Color.red()
        )
        embed.add_field(name="Your troops", value=f"{attacker['troops']:,}", inline=True)
        embed.add_field(name=f"{self.defender_member.display_name}'s troops", value=f"{defender['troops']:,}", inline=True)

        attack_view = AttackView(self.guild_id, self.attacker_member, self.defender_member, attacker["troops"])
        await interaction.response.edit_message(
            content=f"You betrayed your alliance with {self.defender_member.display_name}!",
            embed=embed,
            view=attack_view
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Attack cancelled.", embed=None, view=None)

@bot.tree.command(name="attack", description="Attack another player")
@discord.app_commands.describe(target="Who to attack?")
async def attack(interaction: discord.Interaction, target: discord.Member):
    attacker = get_player(interaction.guild_id, interaction.user.id)
    if not is_active_player(attacker):
        await interaction.response.send_message("You need to /joingame first.", ephemeral=True)
        return
    if target.id == interaction.user.id:
        await interaction.response.send_message("You can't attack yourself gng.", ephemeral=True)
        return
    defender = get_player(interaction.guild_id, target.id)
    if not is_active_player(defender):
        await interaction.response.send_message(f"{target.display_name} isn't a valid target.", ephemeral=True)
        return
    if is_immune(defender):
        remaining = format_remaining(defender["immune_until"] - time.time())
        await interaction.response.send_message(
            f"{target.display_name} still has spawn immunity for {remaining}. You can't attack them yet.", ephemeral=True
        )
        return
    if attacker["troops"] <= 0:
        await interaction.response.send_message("You have no troops to attack with.", ephemeral=True)
        return

    alliances = prune_alliances(attacker)
    save_data(data)
    if str(target.id) in alliances:
        view = BetrayConfirmView(interaction.guild_id, interaction.user, target)
        await interaction.response.send_message(
            content=(
                f"You're currently **allied** with {target.display_name}. Attacking them will break "
                f"the alliance and mark you as a betrayer (you'll take 50% more damage while defending)."
            ),
            view=view,
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"Attack {target.display_name}?",
        description="Choose how many troops to commit to this attack.",
        color=discord.Color.red()
    )
    embed.add_field(name="Your troops", value=f"{attacker['troops']:,}", inline=True)
    embed.add_field(name=f"{target.display_name}'s troops", value=f"{defender['troops']:,}", inline=True)

    view = AttackView(interaction.guild_id, interaction.user, target, attacker["troops"])
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="admingive", description="(Admins) Give or take troops/gold from a player")
@discord.app_commands.describe(target="Who to give to", resource="Troops or gold", amount="Amount (use a negative number to take away)")
@discord.app_commands.choices(resource=[
    discord.app_commands.Choice(name="Troops", value="troops"),
    discord.app_commands.Choice(name="Gold", value="gold"),
])
async def admingive(interaction: discord.Interaction, target: discord.Member, resource: discord.app_commands.Choice[str], amount: int):
    if not has_admin_access(interaction):
        await interaction.response.send_message("❌ You need Manage Server permission to use this.", ephemeral=True)
        return

    player = get_player(interaction.guild_id, target.id)
    if not is_active_player(player):
        await interaction.response.send_message(f"{target.display_name} isn't an active player.", ephemeral=True)
        return

    if resource.value == "troops":
        player["troops"] = max(0, min(player["troops"] + amount, player["troop_cap"]))
        save_data(data)
        await interaction.response.send_message(f"✅ {target.mention}'s troops are now {player['troops']:,} / {player['troop_cap']:,}.")
    else:
        player["gold"] = max(0, player["gold"] + amount)
        save_data(data)
        await interaction.response.send_message(f"✅ {target.mention}'s gold is now {player['gold']:,}.")

class RestartConfirmView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=30)
        self.guild_id = guild_id

    @discord.ui.button(label="Yes, wipe everyone", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = str(self.guild_id)
        data["guilds"][gid] = {"players": {}}
        save_data(data)
        await interaction.response.edit_message(
            content="✅ The game has been reset for this server. Everyone needs to use /joingame again.",
            view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled, nothing was reset.", view=None)

@bot.tree.command(name="adminrestart", description="(Admins) Wipe everyone and restart the game")
async def adminrestart(interaction: discord.Interaction):
    if not has_admin_access(interaction):
        await interaction.response.send_message("❌ You need Manage Server permission to use this.", ephemeral=True)
        return

    view = RestartConfirmView(interaction.guild_id)
    await interaction.response.send_message(
        "⚠️ This will wipe **everyone's** troops, gold, cities, and alliances in this server. "
        "Are you sure?",
        view=view,
        ephemeral=True
    )


@bot.tree.command(name="botstatus", description="(Admins) Set a channel to show a live bot online/offline status")
@discord.app_commands.describe(channel="Which channel should show the bot's status?")
async def botstatus(interaction: discord.Interaction, channel: discord.TextChannel):
    if not has_admin_access(interaction):
        await interaction.response.send_message("❌ You need Manage Server permission to use this.", ephemeral=True)
        return

    message = await channel.send(embed=make_status_embed(True))

    guild_dict = get_guild_dict(interaction.guild_id)
    guild_dict["status_channel_id"] = channel.id
    guild_dict["status_message_id"] = message.id
    save_data(data)

    await interaction.response.send_message(f"✅ Status will now show in {channel.mention} and update itself on startup/shutdown.", ephemeral=True)


@bot.tree.command(name="channellog", description="(Admins) Set a channel for action logs (attacks, missiles, eliminations, alliances)")
@discord.app_commands.describe(channel="Which channel should receive the logs?")
async def channellog(interaction: discord.Interaction, channel: discord.TextChannel):
    if not has_admin_access(interaction):
        await interaction.response.send_message("❌ You need Manage Server permission to use this.", ephemeral=True)
        return

    guild_dict = get_guild_dict(interaction.guild_id)
    guild_dict["log_channel_id"] = channel.id
    save_data(data)

    await interaction.response.send_message(f"✅ Action logs will now be posted in {channel.mention}.", ephemeral=True)


class ClanVisibilityView(discord.ui.View):
    def __init__(self, tag, description, leader_id):
        super().__init__(timeout=120)
        self.tag = tag
        self.description = description
        self.leader_id = leader_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.leader_id:
            await interaction.response.send_message("This isn't your clan setup lil bro.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Public", style=discord.ButtonStyle.success, emoji="🌐")
    async def public_clicked(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.finish(interaction, False)

    @discord.ui.button(label="Invite Only", style=discord.ButtonStyle.primary, emoji="🔒")
    async def invite_only_clicked(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.finish(interaction, True)

    async def finish(self, interaction: discord.Interaction, invite_only):
        if get_clan(self.tag) is not None:
            await interaction.response.edit_message(
                content=f"Someone just took the tag [{self.tag}]. Run /clancenter again and try a different tag.",
                view=None
            )
            return
        existing_tag, _ = find_clan_for_user(self.leader_id)
        if existing_tag is not None:
            await interaction.response.edit_message(content="You're already in a clan.", view=None)
            return

        clan = {
            "tag": self.tag,
            "description": self.description,
            "invite_only": invite_only,
            "leader_id": str(self.leader_id),
            "members": [str(self.leader_id)],
            "bank": {},
            "pending_invites": [],
            "created_at": time.time()
        }
        get_clans_dict()[self.tag] = clan
        save_data(data)

        status = "Invite Only 🔒" if invite_only else "Public 🌐"
        await interaction.response.edit_message(content=f"✅ Clan [{self.tag}] created! Status: {status}", view=None)


class CreateClanModal(discord.ui.Modal, title="Create a Clan"):
    tag_input = discord.ui.TextInput(label="Clan Tag", placeholder="e.g. WOLF", min_length=CLAN_TAG_MIN_LEN, max_length=CLAN_TAG_MAX_LEN)
    desc_input = discord.ui.TextInput(label="Clan Description", style=discord.TextStyle.paragraph, max_length=CLAN_DESC_MAX_LEN)

    async def on_submit(self, interaction: discord.Interaction):
        tag = self.tag_input.value.strip().upper()
        description = self.desc_input.value.strip()

        if not tag.isalnum():
            await interaction.response.send_message("Clan tags can only contain letters and numbers.", ephemeral=True)
            return

        if get_clan(tag) is not None:
            await interaction.response.send_message(f"The tag [{tag}] is already taken. Try a different one.", ephemeral=True)
            return

        existing_tag, _ = find_clan_for_user(interaction.user.id)
        if existing_tag is not None:
            await interaction.response.send_message("You're already in a clan.", ephemeral=True)
            return

        view = ClanVisibilityView(tag, description, interaction.user.id)
        await interaction.response.send_message(
            f"Almost done! Should [{tag}] be public or invite-only?", view=view, ephemeral=True
        )


class ClanSearchModal(discord.ui.Modal, title="Search Clans"):
    query_input = discord.ui.TextInput(label="Clan tag or keyword", required=True, max_length=CLAN_TAG_MAX_LEN)

    def __init__(self, parent_view):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        await self.parent_view.apply_search(interaction, self.query_input.value.strip())


class ClanJoinConfirmView(discord.ui.View):
    def __init__(self, user_id, tag):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.tag = tag
        clan = get_clan(tag)
        invite_only = bool(clan and clan.get("invite_only"))
        if invite_only:
            btn = discord.ui.Button(label="Request Invite", style=discord.ButtonStyle.primary, emoji="✉️")
            btn.callback = self.request_invite
        else:
            btn = discord.ui.Button(label="Join Clan", style=discord.ButtonStyle.success, emoji="✅")
            btn.callback = self.join_clan
        self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your clan menu lil bro.", ephemeral=True)
            return False
        return True

    async def join_clan(self, interaction: discord.Interaction):
        clan = get_clan(self.tag)
        if clan is None:
            await interaction.response.edit_message(content="That clan doesn't exist anymore.", embed=None, view=None)
            return
        if clan.get("invite_only"):
            await interaction.response.edit_message(content="That clan became invite-only. Try again.", embed=None, view=None)
            return
        existing_tag, _ = find_clan_for_user(self.user_id)
        if existing_tag is not None:
            await interaction.response.edit_message(content="You're already in a clan.", embed=None, view=None)
            return

        clan.setdefault("members", []).append(str(self.user_id))
        save_data(data)
        await interaction.response.edit_message(content=f"✅ You joined [{self.tag}]!", embed=None, view=None)

    async def request_invite(self, interaction: discord.Interaction):
        clan = get_clan(self.tag)
        if clan is None:
            await interaction.response.edit_message(content="That clan doesn't exist anymore.", embed=None, view=None)
            return
        existing_tag, _ = find_clan_for_user(self.user_id)
        if existing_tag is not None:
            await interaction.response.edit_message(content="You're already in a clan.", embed=None, view=None)
            return

        pending = clan.setdefault("pending_invites", [])
        uid = str(self.user_id)
        if uid in pending:
            await interaction.response.edit_message(
                content="Invite pending, you've already requested to join this clan.", embed=None, view=None
            )
            return

        pending.append(uid)
        save_data(data)
        await interaction.response.edit_message(
            content=f"✉️ Invite pending, your request to join [{self.tag}] has been sent to the clan leader.",
            embed=None, view=None
        )

        leader_id = clan.get("leader_id")
        try:
            leader_user = await bot.fetch_user(int(leader_id))
            embed = discord.Embed(title="🔔 Clan Join Request", color=discord.Color.blurple())
            embed.description = f"<@{self.user_id}> ({interaction.user.name}) wants to join [{self.tag}]."
            decision_view = ClanInviteDecisionView(self.tag, self.user_id)
            await leader_user.send(embed=embed, view=decision_view)
        except Exception:
            pass


class ClanInviteDecisionView(discord.ui.View):
    def __init__(self, tag, requester_id):
        super().__init__(timeout=None)
        self.tag = tag
        self.requester_id = requester_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        clan = get_clan(self.tag)
        if clan is None:
            await interaction.response.edit_message(content="That clan no longer exists.", embed=None, view=None)
            return
        if interaction.user.id != int(clan.get("leader_id", 0)):
            await interaction.response.send_message("Only the clan leader can respond to this.", ephemeral=True)
            return

        uid = str(self.requester_id)
        pending = clan.setdefault("pending_invites", [])
        if uid in pending:
            pending.remove(uid)

        existing_tag, _ = find_clan_for_user(self.requester_id)
        if existing_tag is not None:
            save_data(data)
            await interaction.response.edit_message(content=f"<@{uid}> already joined another clan.", embed=None, view=None)
            return

        clan.setdefault("members", []).append(uid)
        save_data(data)
        await interaction.response.edit_message(content=f"✅ You accepted <@{uid}> into [{self.tag}].", embed=None, view=None)
        try:
            requester = await bot.fetch_user(int(uid))
            await requester.send(f"✅ You've been accepted into [{self.tag}]!")
        except Exception:
            pass

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        clan = get_clan(self.tag)
        if clan is None:
            await interaction.response.edit_message(content="That clan no longer exists.", embed=None, view=None)
            return
        if interaction.user.id != int(clan.get("leader_id", 0)):
            await interaction.response.send_message("Only the clan leader can respond to this.", ephemeral=True)
            return

        uid = str(self.requester_id)
        pending = clan.setdefault("pending_invites", [])
        if uid in pending:
            pending.remove(uid)
        save_data(data)
        await interaction.response.edit_message(content=f"❌ You rejected <@{uid}>'s request to join [{self.tag}].", embed=None, view=None)
        try:
            requester = await bot.fetch_user(int(uid))
            await requester.send(f"❌ Your request to join [{self.tag}] was rejected.")
        except Exception:
            pass


class ClanJoinBrowseView(discord.ui.View):
    def __init__(self, user_id, page=0, query=None, guild_id=None, mode="join"):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.page = page
        self.query = query
        self.guild_id = guild_id
        self.mode = mode
        self.build_select()

    def build_select(self):
        for item in list(self.children):
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)

        items = sorted_clan_list(self.query)
        total_pages = max(1, (len(items) + CLANS_PER_PAGE - 1) // CLANS_PER_PAGE)
        self.page = max(0, min(self.page, total_pages - 1))
        start = self.page * CLANS_PER_PAGE
        page_items = items[start:start + CLANS_PER_PAGE]

        options = []
        for tag, clan in page_items:
            member_count = len(clan.get("members", []))
            status = "Invite Only" if clan.get("invite_only") else "Public"
            options.append(discord.SelectOption(label=tag, description=f"{member_count} members • {status}"[:100]))

        if options:
            select = discord.ui.Select(placeholder="Pick a clan to view/join", options=options, row=0)
        else:
            select = discord.ui.Select(
                placeholder="No clans on this page",
                options=[discord.SelectOption(label="none", description="none")],
                disabled=True,
                row=0
            )
        select.callback = self.select_clicked
        self.add_item(select)
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your clan menu lil bro.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=1)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self.build_select()
        embed, self.page, total_pages = make_clan_directory_embed(self.page, self.query)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="▶ Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self.build_select()
        embed, self.page, total_pages = make_clan_directory_embed(self.page, self.query)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🔍 Search", style=discord.ButtonStyle.primary, row=1)
    async def search_clicked(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ClanSearchModal(self))

    @discord.ui.button(label="Clear Search", style=discord.ButtonStyle.secondary, row=1)
    async def clear_search(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.query = None
        self.page = 0
        self.build_select()
        embed, self.page, total_pages = make_clan_directory_embed(self.page, self.query)
        await interaction.response.edit_message(embed=embed, view=self)

    async def apply_search(self, interaction: discord.Interaction, query):
        self.query = query
        self.page = 0
        self.build_select()
        embed, self.page, total_pages = make_clan_directory_embed(self.page, self.query)
        await interaction.response.edit_message(embed=embed, view=self)

    async def select_clicked(self, interaction: discord.Interaction):
        select = next(i for i in self.children if isinstance(i, discord.ui.Select))
        tag = select.values[0]
        clan = get_clan(tag)
        if clan is None:
            await interaction.response.send_message("That clan doesn't exist anymore.", ephemeral=True)
            return

        if self.mode == "view":
            embed, page, total_pages = make_clan_hub_embed(self.guild_id, tag, clan, 0, show_bank=False)
            view = ClanViewPager(self.guild_id, self.user_id, tag)
            await interaction.response.edit_message(embed=embed, view=view)
            return

        if self.mode == "war":
            my_tag, my_clan = find_clan_for_user(self.user_id)
            if my_clan is None or str(self.user_id) != str(my_clan.get("leader_id")):
                await interaction.response.edit_message(content="You're not a clan leader anymore.", embed=None, view=None)
                return
            if tag == my_tag:
                await interaction.response.edit_message(content="You can't declare war on your own clan.", embed=None, view=None)
                return
            if tag in my_clan.get("wars", {}):
                await interaction.response.edit_message(content=f"You're already at war with [{tag}].", embed=None, view=None)
                return
            embed = discord.Embed(title=f"Declare war on [{tag}]?", description=clan.get("description", ""), color=discord.Color.dark_red())
            embed.add_field(name="Members", value=str(len(clan.get("members", []))), inline=True)
            embed.add_field(name="Leader", value=f"<@{clan.get('leader_id')}>", inline=True)
            view = ClanWarConfirmView(self.guild_id, self.user_id, my_tag, tag)
            await interaction.response.edit_message(embed=embed, view=view)
            return

        embed = discord.Embed(title=f"[{tag}]", description=clan.get("description", ""), color=discord.Color.dark_gold())
        status = "🔒 Invite Only" if clan.get("invite_only") else "🌐 Public"
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Members", value=str(len(clan.get("members", []))), inline=True)
        embed.add_field(name="Leader", value=f"<@{clan.get('leader_id')}>", inline=True)
        view = ClanJoinConfirmView(self.user_id, tag)
        await interaction.response.edit_message(embed=embed, view=view)


class ClanDepositModal(discord.ui.Modal, title="Deposit Troops"):
    amount_input = discord.ui.TextInput(label="Amount to deposit", placeholder="e.g. 500")

    def __init__(self, guild_id, user_id, tag, page=0):
        super().__init__()
        self.guild_id = guild_id
        self.user_id = user_id
        self.tag = tag
        self.page = page

    async def on_submit(self, interaction: discord.Interaction):
        amount = await parse_amount(interaction, self.amount_input.value.strip())
        if amount is None:
            return

        clan = get_clan(self.tag)
        if not in_clan(clan, self.user_id):
            await interaction.response.send_message("You're not in that clan anymore.", ephemeral=True)
            return

        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message(
                "You need to be an active player in this server to deposit troops here.", ephemeral=True
            )
            return

        if player["troops"] < amount:
            await interaction.response.send_message(f"You only have {player['troops']:,} troops.", ephemeral=True)
            return

        current_bank = get_clan_bank(clan, self.guild_id)
        room = CLAN_BANK_CAP - current_bank
        if amount > room:
            await interaction.response.send_message(
                f"This server's clan bank only has room for {room:,} more troops (cap {CLAN_BANK_CAP:,}).",
                ephemeral=True
            )
            return

        player["troops"] -= amount
        add_clan_bank(clan, self.guild_id, amount)
        save_data(data)

        embed, page, total_pages = make_clan_hub_embed(self.guild_id, self.tag, clan, self.page)
        view = ClanHubView(self.guild_id, self.user_id, self.tag, page)
        await interaction.response.edit_message(embed=embed, view=view)


class ClanWithdrawModal(discord.ui.Modal, title="Withdraw Troops"):
    amount_input = discord.ui.TextInput(label="Amount to withdraw", placeholder="e.g. 500")

    def __init__(self, guild_id, user_id, tag, page=0):
        super().__init__()
        self.guild_id = guild_id
        self.user_id = user_id
        self.tag = tag
        self.page = page

    async def on_submit(self, interaction: discord.Interaction):
        amount = await parse_amount(interaction, self.amount_input.value.strip())
        if amount is None:
            return

        clan = get_clan(self.tag)
        if not in_clan(clan, self.user_id):
            await interaction.response.send_message("You're not in that clan anymore.", ephemeral=True)
            return

        current_bank = get_clan_bank(clan, self.guild_id)
        if current_bank < amount:
            await interaction.response.send_message(
                f"This server's clan bank only has {current_bank:,} troops.", ephemeral=True
            )
            return

        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message(
                "You need to be an active player in this server to withdraw troops here.", ephemeral=True
            )
            return

        room = player["troop_cap"] - player["troops"]
        if amount > room:
            await interaction.response.send_message(
                f"You only have room for {room:,} more troops (troop cap {player['troop_cap']:,}).", ephemeral=True
            )
            return

        add_clan_bank(clan, self.guild_id, -amount)
        player["troops"] += amount
        save_data(data)

        embed, page, total_pages = make_clan_hub_embed(self.guild_id, self.tag, clan, self.page)
        view = ClanHubView(self.guild_id, self.user_id, self.tag, page)
        await interaction.response.edit_message(embed=embed, view=view)


class ClanLeaveConfirmView(discord.ui.View):
    def __init__(self, guild_id, user_id, tag, page=0):
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.user_id = user_id
        self.tag = tag
        self.page = page

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your clan hub lil bro.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Leave", style=discord.ButtonStyle.danger, emoji="🚪")
    async def confirm_clicked(self, interaction: discord.Interaction, button: discord.ui.Button):
        clan = get_clan(self.tag)
        uid = str(self.user_id)
        if not in_clan(clan, self.user_id):
            await interaction.response.edit_message(content="You're not in that clan anymore.", embed=None, view=None)
            return

        members = clan.get("members", [])
        members.remove(uid)

        if str(clan.get("leader_id")) == uid:
            if members:
                clan["leader_id"] = members[0]
                get_clans_dict()[self.tag] = clan
                save_data(data)
                await interaction.response.edit_message(
                    content=f"You left [{self.tag}]. Leadership passed to <@{members[0]}>.", embed=None, view=None
                )
            else:
                del get_clans_dict()[self.tag]
                save_data(data)
                await interaction.response.edit_message(
                    content=f"You left [{self.tag}]. The clan had no other members, so it was disbanded.",
                    embed=None, view=None
                )
        else:
            get_clans_dict()[self.tag] = clan
            save_data(data)
            await interaction.response.edit_message(content=f"You left [{self.tag}].", embed=None, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_clicked(self, interaction: discord.Interaction, button: discord.ui.Button):
        clan = get_clan(self.tag)
        if not in_clan(clan, self.user_id):
            await interaction.response.edit_message(content="You're not in that clan anymore.", embed=None, view=None)
            return
        embed, page, total_pages = make_clan_hub_embed(self.guild_id, self.tag, clan, self.page)
        view = ClanHubView(self.guild_id, self.user_id, self.tag, page)
        await interaction.response.edit_message(embed=embed, view=view)


class ClanWarConfirmView(discord.ui.View):
    def __init__(self, guild_id, user_id, my_tag, enemy_tag):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.user_id = user_id
        self.my_tag = my_tag
        self.enemy_tag = enemy_tag

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your clan menu lil bro.", ephemeral=True)
            return False
        return True

    async def notify_clan(self, tag, title, message):
        clan = get_clan(tag)
        if clan is None:
            return
        for uid in clan.get("members", []):
            try:
                user = await bot.fetch_user(int(uid))
                embed = discord.Embed(title=title, description=message, color=discord.Color.dark_red())
                await user.send(embed=embed)
            except Exception:
                pass

    @discord.ui.button(label="Declare War", style=discord.ButtonStyle.danger, emoji="⚔️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        my_clan = get_clan(self.my_tag)
        enemy_clan = get_clan(self.enemy_tag)
        if my_clan is None or str(self.user_id) != str(my_clan.get("leader_id")):
            await interaction.response.edit_message(content="You're not the leader of that clan anymore.", embed=None, view=None)
            return
        if enemy_clan is None:
            await interaction.response.edit_message(content="That clan no longer exists.", embed=None, view=None)
            return
        if self.enemy_tag in my_clan.get("wars", {}):
            await interaction.response.edit_message(content=f"You're already at war with [{self.enemy_tag}].", embed=None, view=None)
            return

        declare_war(self.my_tag, self.enemy_tag)
        await interaction.response.edit_message(
            content=f"⚔️ [{self.my_tag}] has declared war on [{self.enemy_tag}]! Any shared alliances between the clans were broken.",
            embed=None, view=None
        )
        await self.notify_clan(
            self.enemy_tag,
            "⚔️ Your Clan Is At War!",
            f"[{self.my_tag}] has declared war on [{self.enemy_tag}]. Any alliances between the two clans were broken. "
            f"Fight back to earn war points, or have your leader offer peace to end it."
        )
        await self.notify_clan(
            self.my_tag,
            "⚔️ Your Clan Declared War!",
            f"[{self.my_tag}] has declared war on [{self.enemy_tag}]. Any alliances between the two clans were broken. "
            f"Fight to earn war points, or have your leader offer peace to end it."
        )

        log_embed = discord.Embed(description="# ⚔️ CLAN WAR DECLARED", color=discord.Color.dark_red())
        log_embed.add_field(name="Attacking Clan", value=f"[{self.my_tag}]", inline=True)
        log_embed.add_field(name="Defending Clan", value=f"[{self.enemy_tag}]", inline=True)
        await send_action_log(self.guild_id, log_embed)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="War declaration cancelled.", embed=None, view=None)


class ClanWarView(discord.ui.View):
    def __init__(self, guild_id, user_id, tag):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.user_id = user_id
        self.tag = tag

        clan = get_clan(tag)
        is_leader = clan is not None and str(user_id) == str(clan.get("leader_id"))
        if is_leader:
            declare_btn = discord.ui.Button(label="Declare War", style=discord.ButtonStyle.danger, emoji="⚔️", row=0)
            declare_btn.callback = self.declare_war_clicked
            self.add_item(declare_btn)

            wars = clan.get("wars", {})
            for i, enemy_tag in enumerate(list(wars.keys())[:15]):
                btn = discord.ui.Button(label=f"Peace: {enemy_tag}", style=discord.ButtonStyle.success, emoji="🕊", row=1 + i // 5)
                btn.callback = self._make_peace_callback(enemy_tag)
                self.add_item(btn)

        back_btn = discord.ui.Button(label="Back to Hub", style=discord.ButtonStyle.secondary, row=4)
        back_btn.callback = self.back_clicked
        self.add_item(back_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your clan menu lil bro.", ephemeral=True)
            return False
        return True

    async def declare_war_clicked(self, interaction: discord.Interaction):
        clan = get_clan(self.tag)
        if clan is None or str(self.user_id) != str(clan.get("leader_id")):
            await interaction.response.edit_message(content="You're not the leader of that clan anymore.", embed=None, view=None)
            return
        view = ClanJoinBrowseView(self.user_id, guild_id=self.guild_id, mode="war")
        embed, page, total_pages = make_clan_directory_embed(0)
        await interaction.response.edit_message(embed=embed, view=view)

    def _make_peace_callback(self, enemy_tag):
        async def callback(interaction: discord.Interaction):
            clan = get_clan(self.tag)
            if clan is None or str(self.user_id) != str(clan.get("leader_id")):
                await interaction.response.edit_message(content="You're not the leader of that clan anymore.", embed=None, view=None)
                return
            if enemy_tag not in clan.get("wars", {}):
                await interaction.response.edit_message(content="That war has already ended.", embed=None, view=None)
                return

            end_war(self.tag, enemy_tag)
            await interaction.response.edit_message(
                content=f"🕊 [{self.tag}] has offered peace to [{enemy_tag}]. The war is over.",
                embed=None, view=None
            )

            enemy_clan = get_clan(enemy_tag)
            if enemy_clan is not None:
                for uid in enemy_clan.get("members", []):
                    try:
                        user = await bot.fetch_user(int(uid))
                        await user.send(f"🕊 [{self.tag}] has ended the war with [{enemy_tag}]. Peace has been made.")
                    except Exception:
                        pass
        return callback

    async def back_clicked(self, interaction: discord.Interaction):
        clan = get_clan(self.tag)
        if not in_clan(clan, self.user_id):
            await interaction.response.edit_message(content="You're not in that clan anymore.", embed=None, view=None)
            return
        embed, page, total_pages = make_clan_hub_embed(self.guild_id, self.tag, clan)
        view = ClanHubView(self.guild_id, self.user_id, self.tag, page)
        await interaction.response.edit_message(embed=embed, view=view)


class ClanHubView(discord.ui.View):
    def __init__(self, guild_id, user_id, tag, page=0):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.user_id = user_id
        self.tag = tag
        self.page = page

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your clan hub lil bro.", ephemeral=True)
            return False
        return True

    async def member_clan(self, interaction: discord.Interaction):
        clan = get_clan(self.tag)
        if not in_clan(clan, self.user_id):
            await interaction.response.edit_message(content="You're not in that clan anymore.", embed=None, view=None)
            return None
        return clan

    @discord.ui.button(label="Deposit Troops", style=discord.ButtonStyle.success, emoji="📥", row=0)
    async def deposit_clicked(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.member_clan(interaction) is None:
            return
        await interaction.response.send_modal(ClanDepositModal(self.guild_id, self.user_id, self.tag, self.page))

    @discord.ui.button(label="Withdraw Troops", style=discord.ButtonStyle.primary, emoji="📤", row=0)
    async def withdraw_clicked(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.member_clan(interaction) is None:
            return
        await interaction.response.send_modal(ClanWithdrawModal(self.guild_id, self.user_id, self.tag, self.page))

    @discord.ui.button(label="Leave Clan", style=discord.ButtonStyle.danger, emoji="🚪", row=0)
    async def leave_clicked(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.member_clan(interaction) is None:
            return
        view = ClanLeaveConfirmView(self.guild_id, self.user_id, self.tag, self.page)
        await interaction.response.edit_message(
            content=f"Are you sure you want to leave [{self.tag}]?", embed=None, view=view
        )

    @discord.ui.button(label="Wars", style=discord.ButtonStyle.danger, emoji="⚔️", row=0)
    async def wars_clicked(self, interaction: discord.Interaction, button: discord.ui.Button):
        clan = await self.member_clan(interaction)
        if clan is None:
            return
        embed = make_clan_war_embed(self.tag, clan)
        view = ClanWarView(self.guild_id, self.user_id, self.tag)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=1)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        clan = await self.member_clan(interaction)
        if clan is None:
            return
        self.page -= 1
        embed, self.page, total_pages = make_clan_hub_embed(self.guild_id, self.tag, clan, self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="▶ Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        clan = await self.member_clan(interaction)
        if clan is None:
            return
        self.page += 1
        embed, self.page, total_pages = make_clan_hub_embed(self.guild_id, self.tag, clan, self.page)
        await interaction.response.edit_message(embed=embed, view=self)


class ClanViewPager(discord.ui.View):
    def __init__(self, guild_id, user_id, tag, page=0):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.user_id = user_id
        self.tag = tag
        self.page = page

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your clan menu lil bro.", ephemeral=True)
            return False
        return True

    async def turn(self, interaction: discord.Interaction, delta):
        clan = get_clan(self.tag)
        if clan is None:
            await interaction.response.edit_message(content="That clan no longer exists.", embed=None, view=None)
            return
        self.page += delta
        embed, self.page, total_pages = make_clan_hub_embed(self.guild_id, self.tag, clan, self.page, show_bank=False)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=0)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.turn(interaction, -1)

    @discord.ui.button(label="▶ Next", style=discord.ButtonStyle.secondary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.turn(interaction, 1)


class ClanCenterView(discord.ui.View):
    def __init__(self, guild_id, user_id, page=0):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.user_id = user_id
        self.page = page

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your clan menu lil bro.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Create Clan", style=discord.ButtonStyle.success, emoji="🏰", row=0)
    async def create_clan_clicked(self, interaction: discord.Interaction, button: discord.ui.Button):
        existing_tag, _ = find_clan_for_user(self.user_id)
        if existing_tag is not None:
            await interaction.response.send_message(
                f"You're already in [{existing_tag}]. Leave it before creating a new clan.", ephemeral=True
            )
            return
        await interaction.response.send_modal(CreateClanModal())

    @discord.ui.button(label="Join Clan", style=discord.ButtonStyle.primary, emoji="🤝", row=0)
    async def join_clan_clicked(self, interaction: discord.Interaction, button: discord.ui.Button):
        existing_tag, _ = find_clan_for_user(self.user_id)
        if existing_tag is not None:
            await interaction.response.send_message(f"You're already in [{existing_tag}].", ephemeral=True)
            return
        view = ClanJoinBrowseView(self.user_id)
        embed, page, total_pages = make_clan_directory_embed(0)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="View Clan", style=discord.ButtonStyle.secondary, emoji="👁", row=0)
    async def view_clan_clicked(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ClanJoinBrowseView(self.user_id, guild_id=self.guild_id, mode="view")
        embed, page, total_pages = make_clan_directory_embed(0)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=1)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        embed, self.page, total_pages = make_clan_directory_embed(self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="▶ Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        embed, self.page, total_pages = make_clan_directory_embed(self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Clan Hub", style=discord.ButtonStyle.primary, emoji="🏛", row=1)
    async def clan_hub_clicked(self, interaction: discord.Interaction, button: discord.ui.Button):
        tag, clan = find_clan_for_user(self.user_id)
        if clan is None:
            await interaction.response.send_message("You're not in a clan yet. Use Create Clan or Join Clan first.", ephemeral=True)
            return
        embed, page, total_pages = make_clan_hub_embed(self.guild_id, tag, clan)
        view = ClanHubView(self.guild_id, self.user_id, tag, page)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="clancenter", description="Create, join, or manage a clan (global across servers)")
async def clancenter(interaction: discord.Interaction):
    embed, page, total_pages = make_clan_directory_embed(0)
    view = ClanCenterView(interaction.guild_id, interaction.user.id, page=0)
    await interaction.response.send_message(embed=embed, view=view)


async def main():
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())