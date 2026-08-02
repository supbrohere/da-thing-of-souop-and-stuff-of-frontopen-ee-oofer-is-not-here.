import discord
from discord.ext import commands, tasks
import json
import os
import time
import random

from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

STARTING_TROOPS = 500
STARTING_TROOP_CAP = 5000
TROOP_REGEN_FLOOR = 1.1
TROOP_REGEN_MULTIPLIER = 1.0
CITY_TROOP_CAP_BONUS = 1000
CITY_GOLD_COST = 500
BASE_GOLD_REGEN = 15
GOLD_REGEN_PER_CITY = 5
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
"eliminated": False,
        "alliances": {},
        "betrayer_until": 0
    }
    save_data(data)
    return players[uid]


def gold_regen_for(player):
    return BASE_GOLD_REGEN + (player["cities"] * GOLD_REGEN_PER_CITY)


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


def make_hub_embed(member, player):
    embed = discord.Embed(title=f"{member.display_name}'s land", color=discord.Color.gold())
    embed.add_field(name="Troops", value=f"{player['troops']:,} / {player['troop_cap']:,}", inline=True)
    embed.add_field(name="Gold", value=f"{player['gold']:,}", inline=True)
    embed.add_field(name="Cities", value=str(player["cities"]), inline=True)
    embed.add_field(name="Gold per tick", value=str(gold_regen_for(player)), inline=True)
    embed.set_footer(text=f" Regeneration/tick happens every {TICK_MINUTES} minutes irl.")
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
                content=f"You're not allied with {target.display_name}, so you can't betray them.",
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

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Choose a player to ally with", min_values=1, max_values=1)
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

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your hub lil bro.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Build City (500 gold)", style=discord.ButtonStyle.green, emoji="🏙")
    async def build_city(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message("You can't do that rn.", ephemeral=True)
            return
        if player["gold"] < CITY_GOLD_COST:
            await interaction.response.send_message(
                f"Not enough gold. You need {CITY_GOLD_COST}, you have {player['gold']}.", ephemeral=True
            )
            return
        player["gold"] -= CITY_GOLD_COST
        player["cities"] += 1
        player["troop_cap"] += CITY_TROOP_CAP_BONUS
        save_data(data)
        embed = make_hub_embed(interaction.user, player)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.grey, emoji="🔄")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = get_player(self.guild_id, self.user_id)
        if not is_active_player(player):
            await interaction.response.send_message("You can't do that rn.", ephemeral=True)
            return
        embed = make_hub_embed(interaction.user, player)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Alliance", style=discord.ButtonStyle.blurple, emoji="🤝")
    async def alliance(self, interaction: discord.Interaction, button: discord.ui.Button):
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

@bot.tree.command(name="gamehub", description="manage your lands")
async def gamehub(interaction: discord.Interaction):
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
        await interaction.response.send_message("No one has joined the game yet.", ephemeral=True)
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

        eliminated = False
        if defender["troops"] <= 0 and defender["cities"] <= 0:
            eliminated = True
            defender["eliminated"] = True
            defender["alliances"] = {}

        save_data(data)

        dm_sent = True
        try:
            dm_embed = discord.Embed(
                title="⚔️ Empire Under Attack!",
                description=f"**{self.attacker_member.display_name}** launched an assault on your empire.",
                color=discord.Color.red()
            )
            dm_embed.add_field(name="🪖 Troops Sent Against You", value=f"{attack_troops:,}", inline=True)
            dm_embed.add_field(name="💥 Troops You Lost", value=f"{troop_loss:,}", inline=True)
            dm_embed.add_field(name="🏙 Structures Lost", value=str(1 if captured_city else 0), inline=True)
            if eliminated:
                dm_embed.add_field(name="💀 Status", value="You have been eliminated and can no longer rejoin.", inline=False)
            dm_embed.set_footer(text="Empire Wars")
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


bot.run(TOKEN)