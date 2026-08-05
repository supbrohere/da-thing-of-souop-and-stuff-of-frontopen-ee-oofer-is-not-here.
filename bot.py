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
TROOP_REGEN_MULTIPLIER = 1.0
CITY_TROOP_CAP_BONUS = 1500
CITY_PRICES = [250, 500, 500, 750, 1000, 1000, 1500, 1500, 2000, 2000, 2000, 5000, 5000, 5000, 5000, 5000, 7000, 7000, 7000, 7000, 10000]
BASE_GOLD_REGEN = 20
GOLD_REGEN_PER_CITY = 5
PORT_PRICES = [500, 750, 1000, 1000, 1500, 1500, 2000, 2000, 2000, 5000, 5000, 5000, 5000, 5000, 7500, 7500, 7500, 7500, 10000]
PORT_GOLD_REGEN_PER_PORT = 20
PORT_ALLIANCE_BONUS_PERCENT = 20
TICK_MINUTES = 5
TICK_SECONDS = TICK_MINUTES * 60

DEFENDER_LOSS_MIN = 0.60
DEFENDER_LOSS_MAX = 1.20
ATTACKER_LOSS_MIN = 0.70
ATTACKER_LOSS_MAX = 1.00
BASE_CAPTURE_CHANCE = 0.30
HIGH_CAPTURE_CHANCE = 0.75
ATTACK_PERCENT_OPTIONS = [0.15, 0.30, 0.50, 0.75, 1.0]
ATTACK_PERCENT_LABELS = {0.15: "15%", 0.30: "30%", 0.50: "50%", 0.75: "75%", 1.0: "All In"}
ALLIANCE_DURATION_OPTIONS_DAYS = [2, 3, 4, 5]
BETRAYER_LOSS_MULTIPLIER = 1.5
BETRAYAL_DURATION_SECONDS = 12 * 3600
DATA_FILE = "game_data.json"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def load_data():
    if not os.path.exists(DATA_FILE):
        return {"guilds": {}, "last_tick": time.time()}
    with open(DATA_FILE, "r") as f:
        loaded = json.load(f)
    if "last_tick" not in loaded:
        loaded["last_tick"] = time.time()
    if "guilds" not in loaded:
        loaded["guilds"] = {}
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


def get_player(guild_id, user_id):
    players = get_guild_players(guild_id)
    return players.get(str(user_id))


def is_active_player(player):
    return player is not None and not player.get("eliminated", False)


def create_player(guild_id, user_id):
    players = get_guild_players(guild_id)
    uid = str(user_id)
    players[uid] = {
        "troops": STARTING_TROOPS,
        "troop_cap": STARTING_TROOP_CAP,
        "gold": 0,
        "cities": 0,
        "ports": 0,
        "grid": [{"cities": 0, "ports": 0} for _ in range(9)],
"eliminated": False,
        "alliances": {},
        "betrayer_until": 0
    }
    save_data(data)
    return players[uid]


def active_alliance_count(player, now=None):
    """Counts alliances that haven't expired yet, without mutating the player dict."""
    if now is None:
        now = time.time()
    alliances = player.get("alliances", {})
    return sum(1 for expiry in alliances.values() if expiry > now)


def port_earnings_per_port(player, now=None):
    """Gold/tick a single port earns. Base 20, +20% per active alliance.
    (0 alliances=20/100%, 1=24/120%, 2=28/140%, 3=32/160%, ...)
    Uses integer math throughout so this always lands on a whole number."""
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
    """Price for your NEXT city, based on how many you already own. Past the
    preset list, price just stays at the last value forever (no more scaling)."""
    if current_cities < len(CITY_PRICES):
        return CITY_PRICES[current_cities]
    return CITY_PRICES[-1]


def get_port_price(current_ports):
    """Same idea as get_city_price, just for ports."""
    if current_ports < len(PORT_PRICES):
        return PORT_PRICES[current_ports]
    return PORT_PRICES[-1]


def ensure_grid(player):
    """Players who joined before the grid system won't have a 'grid' key yet.
    Give them one, stacking whatever cities/ports they already own into Stack 1
    so nothing gets lost. Safe to call repeatedly — does nothing once it exists."""
    grid = player.get("grid")
    if isinstance(grid, list) and len(grid) == 9:
        return grid
    grid = [{"cities": 0, "ports": 0} for _ in range(9)]
    grid[0]["cities"] = player.get("cities", 0)
    grid[0]["ports"] = player.get("ports", 0)
    player["grid"] = grid
    return grid


def transfer_structure_box(attacker, defender, structure_key):
    """Moves one captured structure ('cities' or 'ports') out of whichever stack
    the defender has one in, into that SAME stack number on the attacker's grid."""
    ensure_grid(attacker)
    ensure_grid(defender)
    for idx in range(9):
        if defender["grid"][idx][structure_key] > 0:
            defender["grid"][idx][structure_key] -= 1
            attacker["grid"][idx][structure_key] += 1
            return
    # Fallback (shouldn't normally happen): totals said there was one to take,
    # but no stack shows it. Don't lose the structure, just seed Stack 1.
    attacker["grid"][0][structure_key] += 1

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
    """Edits each server's saved status message. If the message got deleted (or there
    isn't one yet), sends a fresh one and remembers its ID for next time."""
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
    """Runs on a clean Ctrl+C / stop signal: flips status messages to offline, then closes the bot."""
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
        # Bot never finished connecting — nothing online to flip offline, just exit normally.
        raise KeyboardInterrupt


signal.signal(signal.SIGINT, _handle_stop_signal)
try:
    signal.signal(signal.SIGTERM, _handle_stop_signal)
except (AttributeError, ValueError):
    pass  # not every platform supports binding SIGTERM, that's fine


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
                "You were eliminated so can't rejoin lmao.", ephemeral=True
            )
        else:
            await interaction.response.send_message("You've already joined the game bro", ephemeral=True)
        return
    create_player(interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(
        f"Welcome, {interaction.user.mention}! You start with "
        f"{STARTING_TROOPS} troops (cap {STARTING_TROOP_CAP}) and 0 gold."
    )

@bot.tree.command(name="help", description="How to use the bot")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="How to use", description="""This is a tutorial written by me (Supbro), this tutorial will be updated every time something new happens. 

So, this bot is based off of openfront.io that is the main reason I built da bot. Do /joingame to join the leaderboard and get access to every other game command. It will start you off with a troop cap of 5000. You can’t really do much at the start and you have to wait for yourself to generate enough gold to buy either a city or a port in /gamehub. Cities increase your troop cap and generate you a little more gold per tick. Ports are different, they increase your base gold gain by 20 per port, however, every alliance you get it adds 20% on top of the base 20 per port. for example, if I have one alliance that means 100% (20) + 20% of 20, that means you get 24 gold per port, that might not seem like a lot, but if you have a lot of ports then it stacks up quick. 

The attacking system is pretty simple, you do /attack, then select a player, after that you can choose from 15% 30%, 50% 75% and all in (100%) for your attacks. When you attack someone it will give the person you attacked a DM message saying that they have been attacked with amount of troops and they lost amount of troops. Attacking people also gives you a chance to destroy a structure. If you have no structures and no troops, then you eliminated, currently there isn’t a way to be revived so you just have to ping an admin. Attacking people also need some luck because the defender can lose up to 120% of the amount relative to the attacker’s troops that they sent, or as low as 60% relative to the attacker’s troops that they sent. The attacker on the other hand can lose up to 100% of their troops, or as low as 70% (the percentages can change, depending on how updated this tutorial is.)

Do /leaderboard to see other people, it is ordered by how much troops they currently have. 
Do /gamehub to manage your land or view other people land
Do /attack to attack other people
Do /joingame to join da game

If you have any other questions, feel free to ping the Admins (if you are not in the MOON server then don’t ping the admin because they probably don’t know much about the bot, no disrespect)""", color=discord.Color.gold())
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
    embed.set_footer(text=f" Regeneration/tick happens every {TICK_MINUTES} minutes irl.")
    return embed


def make_grid_embed(member, player, editable=True):
    ensure_grid(player)
    embed = discord.Embed(
        title=f"{member.display_name}'s Building Stacks",
        color=discord.Color.gold()
    )
    for idx in range(9):
        box = player["grid"][idx]
        embed.add_field(
            name=f"Stack {idx + 1}",
            value=f"🏙 Cities: {box['cities']}\n⚓ Ports: {box['ports']}",
            inline=True
        )
    embed.add_field(name="Total Cities", value=str(player["cities"]), inline=True)
    embed.add_field(name="Total Ports", value=str(player.get("ports", 0)), inline=True)
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
                f"You betrayed {target.display_name}! Alliance broken — you'll take "
                f"50% more troop losses whenever someone attacks you for the next 12 hours."
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
        form_alliance(self.guild_id, self.proposer_id, self.recipient_id, self.days)
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
            view = AllianceProposalView(self.guild_id, self.countering_user_id, self.original_proposer_id, days)
            await interaction.response.edit_message(
                content=(
                    f"<@{self.original_proposer_id}> — <@{self.countering_user_id}> countered with "
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
                lines.append(f"• {name} — expires in {days_left}d {hours_left}h")
        else:
            lines.append("You have no active alliances.")

        view = AllianceHubView(self.guild_id, self.user_id, has_alliances)
        await interaction.response.send_message("\n".join(lines), view=view, ephemeral=True)

class GridBuildView(discord.ui.View):
    """Shown after clicking Build on your own hub — the 3x3 grid plus buttons
    to build a city, build a port, or go back."""
    def __init__(self, guild_id, user_id):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.user_id = user_id

        player = get_player(guild_id, user_id)
        city_price = get_city_price(player["cities"]) if player else 0
        port_price = get_port_price(player.get("ports", 0)) if player else 0

        city_btn = discord.ui.Button(label=f"Build City ({city_price:,} gold)", style=discord.ButtonStyle.green, emoji="🏙")
        city_btn.callback = self.pick_city_spot
        self.add_item(city_btn)

        port_btn = discord.ui.Button(label=f"Build Port ({port_price:,} gold)", style=discord.ButtonStyle.green, emoji="⚓")
        port_btn.callback = self.pick_port_spot
        self.add_item(port_btn)

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


class BuildPositionView(discord.ui.View):
    """The 9 stack-picker buttons (left-to-right, top-to-bottom), plus Cancel."""
    def __init__(self, guild_id, user_id, structure_key):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.user_id = user_id
        self.structure_key = structure_key  # "cities" or "ports"

        for idx in range(9):
            btn = discord.ui.Button(label=str(idx + 1), style=discord.ButtonStyle.blurple, row=idx // 3)
            btn.callback = self._make_callback(idx)
            self.add_item(btn)

        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.grey, row=3)
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

        if self.structure_key == "cities":
            price = get_city_price(player["cities"])
        else:
            price = get_port_price(player.get("ports", 0))

        if player["gold"] < price:
            await interaction.response.send_message(
                f"Not enough gold. You need {price:,}, you have {player['gold']:,}.", ephemeral=True
            )
            return

        player["gold"] -= price
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


class PublicGridView(discord.ui.View):
    """Read-only grid view for someone else's hub — just a Back button."""
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
    """Shown under someone ELSE's hub — read only, just a View Grid button."""
    def __init__(self, guild_id, member: discord.Member):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.member = member

    @discord.ui.button(label="View Grid", style=discord.ButtonStyle.blurple, emoji="🗺")
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


@bot.tree.command(name="leaderboard", description="See the leaderboard")
async def leaderboard(interaction: discord.Interaction):
    players = get_guild_players(interaction.guild_id)
    if not players:
        await interaction.response.send_message("No one has joined the game yet. rip", ephemeral=True)
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
        if pdata.get("eliminated"):
            name += " (eliminated)"
        lines.append(f"{rank_icon} {name} — Troops: {pdata['troops']:,}/{pdata['troop_cap']:,} | Gold: {pdata['gold']:,}")

    embed = discord.Embed(title="da Leaderboard", description="\n".join(lines), color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)


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

        defender_troops_before = defender["troops"]

        defender_loss_rate = random.uniform(DEFENDER_LOSS_MIN, DEFENDER_LOSS_MAX)
        if is_betrayer(defender):
            defender_loss_rate *= BETRAYER_LOSS_MULTIPLIER
        troop_loss = round(attack_troops * defender_loss_rate)
        defender["troops"] = max(0, defender["troops"] - troop_loss)

        attacker_loss_rate = random.uniform(ATTACKER_LOSS_MIN, ATTACKER_LOSS_MAX)
        attacker_loss = round(attack_troops * attacker_loss_rate)
        attacker["troops"] = max(0, attacker["troops"] - attacker_loss)

        capture_chance = HIGH_CAPTURE_CHANCE if defender_troops_before < attack_troops else BASE_CAPTURE_CHANCE
        captured_city = False
        if defender["cities"] > 0 and random.random() < capture_chance:
            captured_city = True
            defender["cities"] -= 1
            defender["troop_cap"] = max(STARTING_TROOP_CAP, defender["troop_cap"] - CITY_TROOP_CAP_BONUS)
            defender["troops"] = min(defender["troops"], defender["troop_cap"])
            attacker["cities"] += 1
            attacker["troop_cap"] += CITY_TROOP_CAP_BONUS
            transfer_structure_box(attacker, defender, "cities")

        eliminated = False
        if defender["troops"] <= 0 and defender["cities"] <= 0:
            eliminated = True
            defender["eliminated"] = True
            defender["alliances"] = {}

        save_data(data)

        dm_sent = True
        try:
            dm_embed = discord.Embed(
                title="⚔️Ur Under Attack!⚔️",
                description=f"**{self.attacker_member.display_name}** launched an invasion of your lands, Beat em up",
                color=discord.Color.red()
            )
            dm_embed.add_field(name="🪖 Troops Sent Against You", value=f"{attack_troops:,}", inline=True)
            dm_embed.add_field(name="💥 Troops You Lost", value=f"{troop_loss:,}", inline=True)
            dm_embed.add_field(name="🏙 Structures Lost", value=str(1 if captured_city else 0), inline=True)
            if eliminated:
                dm_embed.add_field(name="💀 Status", value="You have been eliminated and can no longer rejoin.", inline=False)
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
            result_lines.append("You captured one of their cities W attack gng")
        if eliminated:
            result_lines.append(f"{self.defender_member.display_name} has been eliminated!")

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

@bot.tree.command(name="adminrevive", description="(Admins) Revive player back")
@discord.app_commands.checks.has_permissions(manage_guild=True)
async def adminrevive(interaction: discord.Interaction, target: discord.Member):
    player = get_player(interaction.guild_id, target.id)
    if player is None:
        await interaction.response.send_message(
            f"{target.display_name} has never joined the game — nothing to revive.", ephemeral=True
        )
        return

    player["troops"] = STARTING_TROOPS
    player["troop_cap"] = STARTING_TROOP_CAP
    player["gold"] = 0
    player["cities"] = 0
    player["ports"] = 0
    player["grid"] = [{"cities": 0, "ports": 0} for _ in range(9)]
    player["eliminated"] = False
    save_data(data)

    await interaction.response.send_message(
        f"✅ {target.mention} has been revived with a fresh empire "
        f"({STARTING_TROOPS} troops, cap {STARTING_TROOP_CAP}, 0 gold, 0 cities)."
    )

@adminrevive.error
async def adminrevive_error(interaction: discord.Interaction, error):
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need Manage Server permission to use this.", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ Something went wrong: {error}", ephemeral=True)

@bot.tree.command(name="admingive", description="(Admins) Give or take troops/gold from a player")
@discord.app_commands.describe(target="Who to give to", resource="Troops or gold", amount="Amount (use a negative number to take away)")
@discord.app_commands.choices(resource=[
    discord.app_commands.Choice(name="Troops", value="troops"),
    discord.app_commands.Choice(name="Gold", value="gold"),
])
@discord.app_commands.checks.has_permissions(manage_guild=True)
async def admingive(interaction: discord.Interaction, target: discord.Member, resource: discord.app_commands.Choice[str], amount: int):
    player = get_player(interaction.guild_id, target.id)
    if not is_active_player(player):
        await interaction.response.send_message(f"{target.display_name} isn't an active player.", ephemeral=True)
        return

    if resource.value == "troops":
        player["troops"] = max(0, min(player["troops"] + amount, player["troop_cap"]))
        save_data(data)
        await interaction.response.send_message(
            f"✅ {target.mention}'s troops are now {player['troops']:,} / {player['troop_cap']:,}."
        )
    else:
        player["gold"] = max(0, player["gold"] + amount)
        save_data(data)
        await interaction.response.send_message(
            f"✅ {target.mention}'s gold is now {player['gold']:,}."
        )

@admingive.error
async def admingive_error(interaction: discord.Interaction, error):
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need Manage Server permission to use this.", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ Something went wrong: {error}", ephemeral=True)

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
        await interaction.response.edit_message(content="Cancelled — nothing was reset.", view=None)

@bot.tree.command(name="adminrestart", description="(Admins) Wipe everyone and restart the game")
@discord.app_commands.checks.has_permissions(manage_guild=True)
async def adminrestart(interaction: discord.Interaction):
    view = RestartConfirmView(interaction.guild_id)
    await interaction.response.send_message(
        "⚠️ This will wipe **everyone's** troops, gold, cities, and alliances in this server. "
        "Are you sure?",
        view=view,
        ephemeral=True
    )

@adminrestart.error
async def adminrestart_error(interaction: discord.Interaction, error):
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need Manage Server permission to use this.", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ Something went wrong: {error}", ephemeral=True)


@bot.tree.command(name="botstatus", description="(Admins) Set a channel to show a live bot online/offline status")
@discord.app_commands.describe(channel="Which channel should show the bot's status?")
@discord.app_commands.checks.has_permissions(manage_guild=True)
async def botstatus(interaction: discord.Interaction, channel: discord.TextChannel):
    message = await channel.send(embed=make_status_embed(True))

    guild_dict = get_guild_dict(interaction.guild_id)
    guild_dict["status_channel_id"] = channel.id
    guild_dict["status_message_id"] = message.id
    save_data(data)

    await interaction.response.send_message(f"✅ Status will now show in {channel.mention} and update itself on startup/shutdown.", ephemeral=True)

@botstatus.error
async def botstatus_error(interaction: discord.Interaction, error):
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need Manage Server permission to use this.", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ Something went wrong: {error}", ephemeral=True)


async def main():
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())