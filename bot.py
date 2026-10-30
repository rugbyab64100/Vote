import os
import sqlite3
import time
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
import discord
from discord import app_commands
from discord.ext import commands, tasks

TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = os.getenv("DB_PATH", "/data/vote_role_bot.sqlite3")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

def db_connect():
    # Ensure the parent directory exists when using a custom DB_PATH.
    parent = os.path.dirname(DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS role_expirations (
            guild_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            PRIMARY KEY (guild_id, member_id, role_id)
        )
    """)
    conn.commit()
    return conn

def schedule_expiration(guild_id: int, member_id: int, role_id: int, expires_at: int):
    with db_connect() as conn:
        conn.execute("""
            INSERT INTO role_expirations (guild_id, member_id, role_id, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, member_id, role_id)
            DO UPDATE SET expires_at = excluded.expires_at
        """, (guild_id, member_id, role_id, expires_at))
        conn.commit()

class RoleVoteView(discord.ui.View):
    def __init__(self, target: discord.Member, role: discord.Role,
                 required_yes: int, vote_seconds: int, action: str,
                 role_seconds: int = 0):
        super().__init__(timeout=vote_seconds)
        self.target = target
        self.role = role
        self.required_yes = required_yes
        self.action = action  # "punition" or "gentil"
        self.role_seconds = role_seconds
        self.yes_voters = set()
        self.no_voters = set()
        self.message = None
        self.finished = False

    def vote_count_text(self):
        return (
            f"**Pour :** {len(self.yes_voters)}/{self.required_yes} requis\n"
            f"**Contre :** {len(self.no_voters)}\n"
            f"**Cible :** {self.target.mention} (ne peut pas voter)"
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.target.id:
            await interaction.response.send_message(
                "Tu es la personne visée par ce vote : tu ne peux pas voter.",
                ephemeral=True
            )
            return False
        if interaction.user.bot:
            await interaction.response.send_message(
                "Les bots ne peuvent pas participer à ce vote.", ephemeral=True
            )
            return False
        if interaction.user.id in self.yes_voters or interaction.user.id in self.no_voters:
            await interaction.response.send_message(
                "Tu as déjà voté dans ce sondage.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Pour", style=discord.ButtonStyle.success, emoji="✅")
    async def vote_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.yes_voters.add(interaction.user.id)
        if len(self.yes_voters) >= self.required_yes:
            await interaction.response.defer()
            await self.pass_vote()
        else:
            await interaction.response.edit_message(content=self.vote_count_text(), view=self)

    @discord.ui.button(label="Contre", style=discord.ButtonStyle.danger, emoji="❌")
    async def vote_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.no_voters.add(interaction.user.id)
        await interaction.response.edit_message(content=self.vote_count_text(), view=self)

    async def pass_vote(self):
        if self.finished:
            return
        self.finished = True
        self.stop()
        guild = self.target.guild
        try:
            if self.action == "punition":
                await self.target.add_roles(self.role, reason="Seuil de votes favorables atteint")
                expiry = int(time.time()) + self.role_seconds
                schedule_expiration(guild.id, self.target.id, self.role.id, expiry)
                # Afficher l'heure de fin selon le fuseau horaire français.
                paris_tz = ZoneInfo("Europe/Paris")
                now_local = datetime.now(paris_tz)
                end_local = datetime.fromtimestamp(expiry, paris_tz)
                end_time = end_local.strftime("%Hh%M")
                if end_local.date() == now_local.date():
                    end_label = f"jusqu'à {end_time}"
                else:
                    day = f"{end_local.day}er" if end_local.day == 1 else str(end_local.day)
                    end_label = f"jusqu'au {day} {end_local.strftime('%B')} à {end_time}"
                result = (f"✅ **Vote accepté !** La punition est appliquée à {self.target.mention} "
                          f"{end_label}.")
            else:
                await self.target.remove_roles(self.role, reason="Vote accepté pour lever la punition")
                with db_connect() as conn:
                    conn.execute(
                        "DELETE FROM role_expirations WHERE guild_id=? AND member_id=? AND role_id=?",
                        (guild.id, self.target.id, self.role.id)
                    )
                    conn.commit()
                result = f"✅ **Vote accepté !** La punition de {self.target.mention} est levée."
            await self.message.edit(content=result + "\n\n" + self.vote_count_text(), view=None)
        except discord.Forbidden:
            await self.message.edit(
                content=("❌ Le vote a atteint le seuil, mais je n'ai pas la permission de modifier le rôle. "
                         "Vérifie ma permission « Gérer les rôles » et place mon rôle au-dessus de PUNI."),
                view=None
            )
        except discord.HTTPException as exc:
            print(f"Erreur Discord pendant l'application du résultat du vote : {exc}")

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    content="⌛ **Vote terminé : seuil de votes favorables non atteint.**\n\n"
                            + self.vote_count_text(),
                    view=self
                )
            except discord.HTTPException:
                pass

@bot.event
async def on_ready():
    db_connect().close()
    if not expire_roles.is_running():
        expire_roles.start()
    try:
        synced = await bot.tree.sync()
        print(f"Connecté en tant que {bot.user}; {len(synced)} commande(s) synchronisée(s).")
    except Exception as exc:
        print(f"Échec de synchronisation des commandes : {exc}")

@tasks.loop(seconds=30)
async def expire_roles():
    now = int(time.time())
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT guild_id, member_id, role_id FROM role_expirations WHERE expires_at <= ?",
            (now,)
        ).fetchall()
    for guild_id, member_id, role_id in rows:
        guild = bot.get_guild(guild_id)
        if guild is None:
            continue
        role = guild.get_role(role_id)
        if role is None:
            with db_connect() as conn:
                conn.execute(
                    "DELETE FROM role_expirations WHERE guild_id=? AND member_id=? AND role_id=?",
                    (guild_id, member_id, role_id)
                )
                conn.commit()
            continue
        try:
            member = guild.get_member(member_id) or await guild.fetch_member(member_id)
            await member.remove_roles(role, reason="Durée du rôle temporaire écoulée")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            print(f"Impossible de retirer le rôle {role_id} du membre {member_id}: {exc}")
            # Keep the row if Discord rejected the action, so a later retry can happen.
            continue
        with db_connect() as conn:
            conn.execute(
                "DELETE FROM role_expirations WHERE guild_id=? AND member_id=? AND role_id=?",
                (guild_id, member_id, role_id)
            )
            conn.commit()

@expire_roles.before_loop
async def before_expire_roles():
    await bot.wait_until_ready()

def get_puni_role(guild: discord.Guild):
    return discord.utils.get(guild.roles, name="PUNI")


def get_vote_channel(guild: discord.Guild):
    return discord.utils.find(
        lambda ch: ch.name.lower().replace("-", " ").replace("_", " ") == "vote en cours",
        guild.text_channels
    )


async def check_vote_setup(interaction: discord.Interaction, membre: discord.Member):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return None
    if membre.id == interaction.user.id:
        await interaction.response.send_message("Tu ne peux pas lancer un vote sur toi-même.", ephemeral=True)
        return None
    if membre.bot:
        await interaction.response.send_message("Tu ne peux pas lancer un vote sur un bot.", ephemeral=True)
        return None
    role = get_puni_role(interaction.guild)
    if role is None or role.is_default() or role.managed:
        await interaction.response.send_message("Je ne trouve pas de rôle **PUNI** attribuable sur ce serveur.", ephemeral=True)
        return None
    channel = get_vote_channel(interaction.guild)
    if channel is None:
        await interaction.response.send_message("Je ne trouve pas le salon **#vote-en-cours**. Crée-le puis réessaie.", ephemeral=True)
        return None
    bot_member = interaction.guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        await interaction.response.send_message("Je n'ai pas la permission « Gérer les rôles ».", ephemeral=True)
        return None
    if role >= bot_member.top_role:
        await interaction.response.send_message("Place le rôle du bot au-dessus de **PUNI** dans les paramètres des rôles.", ephemeral=True)
        return None
    if membre.top_role >= bot_member.top_role:
        await interaction.response.send_message("Je ne peux pas modifier les rôles de ce membre : son rôle le plus élevé est au-dessus du mien ou au même niveau.", ephemeral=True)
        return None
    return role, channel


async def publish_vote(interaction: discord.Interaction, membre: discord.Member,
                       votes_positifs: int, duree_vote: int, action: str,
                       duree_role: int = 0):
    setup = await check_vote_setup(interaction, membre)
    if setup is None:
        return
    role, channel = setup
    if action == "punition" and role in membre.roles:
        await interaction.response.send_message(f"{membre.mention} possède déjà le rôle **PUNI**.", ephemeral=True)
        return
    if action == "gentil" and role not in membre.roles:
        await interaction.response.send_message(f"{membre.mention} ne possède pas le rôle **PUNI**.", ephemeral=True)
        return

    view = RoleVoteView(
        target=membre, role=role, required_yes=votes_positifs,
        vote_seconds=duree_vote * 60, action=action, role_seconds=duree_role * 60
    )
    title = "Vote de punition" if action == "punition" else "Vote pour lever la punition"
    request = "une punition pour" if action == "punition" else "à lever la punition de"
    details = f"**Durée de la punition si acceptée :** {duree_role} minute(s)\n" if action == "punition" else ""
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        message = await channel.send(
            f"🗳️ **{title}**\n"
            f"Quelqu’un demande {request} {membre.mention}.\n\n"
            f"**Seuil requis :** {votes_positifs} vote(s) « Pour »\n"
            f"**Durée du vote :** {duree_vote} minute(s)\n"
            f"{details}\n" + view.vote_count_text(),
            view=view
        )
        view.message = message
        await interaction.followup.send(f"Vote publié dans {channel.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(f"Je n'ai pas la permission d'envoyer un message dans {channel.mention}.", ephemeral=True)


@bot.tree.command(name="punition", description="Lancer un vote pour appliquer une punition temporaire.")
@app_commands.describe(
    membre="La personne qui recevra la punition si le vote réussit",
    votes_positifs="Nombre de votes « Pour » requis (minimum 3)",
    duree_vote="Durée du vote en minutes (1 à 5)",
    duree_role="Durée de la punition en minutes (1 à 120)"
)
@app_commands.guild_only()
async def vote_role(
    interaction: discord.Interaction,
    membre: discord.Member,
    votes_positifs: app_commands.Range[int, 3, 1000],
    duree_vote: app_commands.Range[int, 1, 5],
    duree_role: app_commands.Range[int, 1, 120],
):
    await publish_vote(interaction, membre, votes_positifs, duree_vote, "punition", duree_role)


@bot.tree.command(name="gentil", description="Lancer un vote pour lever la punition d'un membre.")
@app_commands.describe(
    membre="La personne dont la punition pourrait être levée",
    votes_positifs="Nombre de votes « Pour » requis (minimum 3)",
    duree_vote="Durée du vote en minutes (1 à 5)"
)
@app_commands.guild_only()
async def vote_gentil(
    interaction: discord.Interaction,
    membre: discord.Member,
    votes_positifs: app_commands.Range[int, 3, 1000],
    duree_vote: app_commands.Range[int, 1, 5],
):
    await publish_vote(interaction, membre, votes_positifs, duree_vote, "gentil")

if not TOKEN:
    raise RuntimeError("La variable d'environnement DISCORD_TOKEN est manquante.")
bot.run(TOKEN)
