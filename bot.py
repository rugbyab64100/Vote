import os
import sqlite3
import time
import asyncio
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
                 required_yes: int, vote_seconds: int, role_seconds: int):
        super().__init__(timeout=vote_seconds)
        self.target = target
        self.role = role
        self.required_yes = required_yes
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
            await self.target.add_roles(self.role, reason="Seuil de votes favorables atteint")
            expiry = int(time.time()) + self.role_seconds
            schedule_expiration(guild.id, self.target.id, self.role.id, expiry)
            await self.message.edit(
                content=(
                    "✅ **Vote accepté !** La punition est appliquée à "
                    f"{self.target.mention} pendant {self.role_seconds // 60} min"
                    + (f" {self.role_seconds % 60} s" if self.role_seconds % 60 else "")
                    + ".\n\n" + self.vote_count_text()
                ),
                view=None
            )
        except discord.Forbidden:
            await self.message.edit(
                content=("❌ Le vote a atteint le seuil, mais je n'ai pas la permission "
                         "d'attribuer ce rôle. Vérifie ma permission « Gérer les rôles » "
                         "et place mon rôle au-dessus du rôle à attribuer."),
                view=None
            )
        except discord.HTTPException:
            pass

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

@bot.tree.command(name="punition", description="Lancer un vote pour appliquer une punition temporaire à un membre.")
@app_commands.describe(
    membre="La personne qui recevra la punition si le vote réussit",
    votes_positifs="Nombre de votes « Pour » requis",
    duree_vote="Durée du vote en minutes (1 à 1440)",
    duree_role="Durée de la punition en minutes (1 à 43200)"
)
@app_commands.guild_only()
async def vote_role(
    interaction: discord.Interaction,
    membre: discord.Member,
    votes_positifs: app_commands.Range[int, 1, 1000],
    duree_vote: app_commands.Range[int, 1, 1440],
    duree_role: app_commands.Range[int, 1, 43200],
):
    if membre.id == interaction.user.id:
        await interaction.response.send_message(
            "Tu ne peux pas lancer un vote pour t'attribuer toi-même ce rôle.",
            ephemeral=True
        )
        return
    if membre.bot:
        await interaction.response.send_message(
            "Tu ne peux pas lancer ce vote pour un bot.", ephemeral=True
        )
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    role = discord.utils.get(interaction.guild.roles, name="PUNI")
    if role is None:
        await interaction.response.send_message(
            "Je ne trouve pas le rôle **PUNI** sur ce serveur. Vérifie son nom exact.",
            ephemeral=True
        )
        return
    if role.is_default() or role.managed:
        await interaction.response.send_message(
            "Le rôle **PUNI** ne peut pas être attribué par cette commande.", ephemeral=True
        )
        return

    channel = discord.utils.find(
        lambda ch: ch.name.lower().replace("-", " ").replace("_", " ") == "vote en cours",
        interaction.guild.text_channels
    )
    if channel is None:
        await interaction.response.send_message(
            "Je ne trouve pas le salon **#vote-en-cours** (ou **#vote en cours**). Crée-le puis réessaie.",
            ephemeral=True
        )
        return

    bot_member = interaction.guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        await interaction.response.send_message(
            "Je n'ai pas la permission « Gérer les rôles ». Demande à un administrateur de me l'accorder.",
            ephemeral=True
        )
        return
    if role >= bot_member.top_role:
        await interaction.response.send_message(
            "Je ne peux pas gérer ce rôle : place mon rôle au-dessus de celui-ci dans Paramètres du serveur → Rôles.",
            ephemeral=True
        )
        return
    if membre.top_role >= bot_member.top_role:
        await interaction.response.send_message(
            "Je ne peux pas attribuer de rôle à ce membre car son rôle le plus élevé est au-dessus du mien ou au même niveau.",
            ephemeral=True
        )
        return

    view = RoleVoteView(
        target=membre,
        role=role,
        required_yes=votes_positifs,
        vote_seconds=duree_vote * 60,
        role_seconds=duree_role * 60
    )
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        message = await channel.send(
            f"🗳️ **Vote de punition**\n"
            f"{interaction.user.mention} demande une punition pour {membre.mention}.\n\n"
            f"**Seuil requis :** {votes_positifs} vote(s) « Pour »\n"
            f"**Durée du vote :** {duree_vote} minute(s)\n"
            f"**Durée de la punition si acceptée :** {duree_role} minute(s)\n\n"
            + view.vote_count_text(),
            view=view
        )
        view.message = message
        await interaction.followup.send(f"Vote publié dans {channel.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            f"Je n'ai pas la permission d'envoyer un message dans {channel.mention}.",
            ephemeral=True
        )

if not TOKEN:
    raise RuntimeError("La variable d'environnement DISCORD_TOKEN est manquante.")
bot.run(TOKEN)
