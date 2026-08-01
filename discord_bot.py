"""
Discord bot: auto-approve / auto-reject NASA Trek measurement screenshots.

SETUP
-----
1. pip install -U discord.py google-generativeai pillow --break-system-packages
2. In the Discord Developer Portal (discord.com/developers/applications):
   - Create/select your application -> Bot tab
   - Turn ON "Message Content Intent" (bot can't read attachments without this)
   - Copy the bot token
3. Set environment variables before running:
     export DISCORD_BOT_TOKEN="your-bot-token"
     export GEMINI_API_KEY="your-gemini-key"
   (On Windows PowerShell: $env:DISCORD_BOT_TOKEN="...")
4. Invite the bot to your server with at least these permissions:
   View Channels, Send Messages, Embed Links, Read Message History,
   Attach Files (optional, if you want it to send anything back)
5. Run: python discord_bot.py

WHAT IT DOES
------------
Watches every channel the bot can see. Whenever someone posts an image
attachment with #ge-sp-marstrek in the same message, the bot downloads
it in memory, runs it through the same classifier as the batch script,
replies with an approved/rejected embed + reasons, and appends the
result to discord_results_log.csv.

Anyone can type "!export" in any channel, but only members with the
"Mars Reviewer" role (or server Administrator permission) will
actually receive the CSV file — everyone else gets a permission
denied message. Change the required role name via the Railway
variable AUTHORIZED_ROLE.

Every APPROVED submission also gets auto-posted to a review channel
(default name "admin-review", set via ADMIN_REVIEW_CHANNEL) with the
submitted image shown inline, plus two buttons:
  - "Approve +100 Karma" -> awards DEFAULT_KARMA_POINTS to the
    submitter automatically (no typing needed)
  - "Flag as Rejected" -> marks it reviewed with no karma awarded
Only members with AUTHORIZED_ROLE (or Administrator) can click these.
Change the point value via the Railway variable DEFAULT_KARMA_POINTS.

Admins can still manually award/check karma with:
  !karma @user 50 [optional reason]   (award — authorized only)
  !karma @user                        (check total — anyone)

PERSISTENT STORAGE (Railway)
-----------------------------
By default the CSV lives inside the container's filesystem, which is
WIPED on every redeploy/restart. To keep your log permanently:
  1. In Railway, open your service -> Settings -> Volumes -> Add Volume
  2. Set the mount path to /data
  3. Add an environment variable: DATA_DIR = /data
  4. Redeploy
From then on the CSV survives restarts and redeploys.
"""
import discord
import os
import csv
import asyncio
import re
from datetime import datetime, timezone

from mars_checker import process_image_bytes

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
if not TOKEN:
    raise RuntimeError(
        "No Discord bot token found. Set the DISCORD_BOT_TOKEN environment "
        "variable before running this script."
    )

# DATA_DIR lets you point the CSV at a persistent Railway Volume instead of
# the container's throwaway filesystem — see setup notes above.
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
LOG_CSV      = os.path.join(DATA_DIR, "discord_results_log.csv")
KARMA_CSV    = os.path.join(DATA_DIR, "karma_log.csv")
IMAGE_EXTS   = (".png", ".jpg", ".jpeg", ".webp")
REQUIRED_TAG   = "#ge-sp-marstrek"  # image must be posted with this tag to be checked
EXPORT_COMMAND = "!export"          # posting this sends the current log as a file
KARMA_COMMAND  = "!karma"           # !karma @user 50 [reason]  or  !karma @user (to check total)

# Approved submissions get auto-posted here for admin review.
# Set via Railway variable ADMIN_REVIEW_CHANNEL — must match the channel
# name exactly (without the #). Create this channel in each server.
ADMIN_REVIEW_CHANNEL = os.environ.get("ADMIN_REVIEW_CHANNEL", "admin-review")

# Only members with this role (or server Administrator permission) can !export.
# Set via Railway variable AUTHORIZED_ROLE — must match the role name exactly
# (case-insensitive). Create this role in each server and assign it to
# whoever should be able to see the data.
AUTHORIZED_ROLE = os.environ.get("AUTHORIZED_ROLE", "Mars Reviewer")

# Points auto-awarded when an admin clicks "Approve" in the review channel.
# Set via Railway variable DEFAULT_KARMA_POINTS.
DEFAULT_KARMA_POINTS = int(os.environ.get("DEFAULT_KARMA_POINTS", "100"))



def is_authorized(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(role.name.lower() == AUTHORIZED_ROLE.lower() for role in member.roles)

FIELDNAMES = [
    "timestamp", "discord_user", "channel", "file",
    "decision", "failure_count", "all_reasons", "all_messages",
    "address_bar_visible", "url_x", "url_y",
    "olympus_mons", "latitude", "longitude",
    "terrain_distance_km", "measurement_line_visible",
    "your_verdict", "model_was_correct", "notes",
]

intents = discord.Intents.default()
intents.message_content = True  # required to read attachments
client = discord.Client(intents=intents)


def log_result(user: str, channel: str, result: dict) -> None:
    file_exists = os.path.exists(LOG_CSV)
    with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        row = {k: result.get(k, "") for k in FIELDNAMES}
        row["timestamp"]     = datetime.now(timezone.utc).isoformat()
        row["discord_user"]  = user
        row["channel"]       = channel
        row["your_verdict"]      = ""
        row["model_was_correct"] = ""
        row["notes"]              = ""
        writer.writerow(row)


KARMA_FIELDNAMES = ["timestamp", "discord_user_id", "discord_user", "points", "awarded_by", "reason"]


def award_karma(user_id: int, user_display: str, points: int, awarded_by: str, reason: str) -> int:
    """Appends a karma award and returns the user's new running total."""
    file_exists = os.path.exists(KARMA_CSV)
    with open(KARMA_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=KARMA_FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "discord_user_id": user_id,
            "discord_user": user_display,
            "points": points,
            "awarded_by": awarded_by,
            "reason": reason,
        })
    return get_karma_total(user_id)


def get_karma_total(user_id: int) -> int:
    if not os.path.exists(KARMA_CSV):
        return 0
    total = 0
    with open(KARMA_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("discord_user_id") == str(user_id):
                try:
                    total += int(row.get("points", 0))
                except ValueError:
                    pass
    return total


def build_review_embed(result: dict, submitter: discord.Member, attachment: discord.Attachment) -> discord.Embed:
    embed = discord.Embed(
        title="🔔 Pending Karma Review",
        description=f"Submitted by {submitter.mention}\nFile: `{result['file']}`",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="Terrain distance",
        value=f"{result['terrain_distance_km']} km" if result["terrain_distance_km"] is not None else "—",
        inline=True,
    )
    embed.add_field(
        name="Lat / Lon",
        value=f"{result['latitude']} / {result['longitude']}",
        inline=True,
    )
    embed.set_image(url=attachment.url)
    return embed


class ReviewView(discord.ui.View):
    """Buttons shown under a pending-review post. No slash/text commands needed."""

    def __init__(self, submitter_id: int, submitter_display: str, filename: str):
        super().__init__(timeout=None)
        self.submitter_id = submitter_id
        self.submitter_display = submitter_display
        self.filename = filename
        self.resolved = False

    async def _check_authorized(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_authorized(interaction.user):
            await interaction.response.send_message(
                f"Only members with the **{AUTHORIZED_ROLE}** role can review submissions.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label=f"Approve +{DEFAULT_KARMA_POINTS} Karma", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.resolved:
            await interaction.response.send_message("This submission was already reviewed.", ephemeral=True)
            return
        if not await self._check_authorized(interaction):
            return
        self.resolved = True
        new_total = award_karma(
            self.submitter_id, self.submitter_display, DEFAULT_KARMA_POINTS,
            str(interaction.user), f"auto-approved via admin-review: {self.filename}",
        )
        for item in self.children:
            item.disabled = True
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(
            name="Result",
            value=f"✅ Approved by {interaction.user.mention} — +{DEFAULT_KARMA_POINTS} karma (new total: {new_total})",
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Flag as Rejected", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.resolved:
            await interaction.response.send_message("This submission was already reviewed.", ephemeral=True)
            return
        if not await self._check_authorized(interaction):
            return
        self.resolved = True
        for item in self.children:
            item.disabled = True
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.add_field(
            name="Result",
            value=f"❌ Flagged as rejected by {interaction.user.mention} — no karma awarded",
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=self)


def build_embed(result: dict) -> discord.Embed:
    approved = result["decision"] == "approved"
    embed = discord.Embed(
        title=f"{'⏳ Initiated for Approval' if approved else '❌ Rejected'} — {result['file']}",
        description="All checks passed — sent to admins for karma review." if approved else None,
        color=discord.Color.gold() if approved else discord.Color.red(),
    )
    embed.add_field(
        name="Terrain distance",
        value=f"{result['terrain_distance_km']} km" if result["terrain_distance_km"] is not None else "—",
        inline=True,
    )
    embed.add_field(
        name="Lat / Lon",
        value=f"{result['latitude']} / {result['longitude']}",
        inline=True,
    )
    embed.add_field(
        name="URL x / y",
        value=f"{result['url_x']} / {result['url_y']}",
        inline=True,
    )
    if not approved and result.get("all_messages"):
        for i, msg in enumerate(result["all_messages"].split(" | "), 1):
            embed.add_field(name=f"Issue {i}", value=msg, inline=False)
    return embed


@client.event
async def on_ready():
    print(f"Logged in as {client.user} — watching for image uploads.")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # !karma @user 50 [reason]   -> award points (authorized only)
    # !karma @user               -> check their current total (anyone)
    if message.content.strip().lower().startswith(KARMA_COMMAND):
        if not message.mentions:
            await message.channel.send(
                f"Usage: `{KARMA_COMMAND} @user <points> [reason]` to award, "
                f"or `{KARMA_COMMAND} @user` to check their total."
            )
            return
        target = message.mentions[0]
        rest = message.content.split(target.mention, 1)[-1].strip()
        # also handle the plain <@id> mention form discord.py may not have stripped
        rest = re.sub(r"^<@!?\d+>\s*", "", rest).strip()

        if not rest:
            total = get_karma_total(target.id)
            await message.channel.send(f"{target.mention} has **{total} karma** points.")
            return

        if not isinstance(message.author, discord.Member) or not is_authorized(message.author):
            await message.channel.send(
                f"{message.author.mention} only reviewers can award karma. Ask an admin "
                f"for the **{AUTHORIZED_ROLE}** role if you need access."
            )
            return

        match = re.match(r"([+-]?\d+)\s*(.*)", rest)
        if not match:
            await message.channel.send(f"Couldn't read a point value from `{rest}`.")
            return
        points = int(match.group(1))
        reason = match.group(2).strip()

        new_total = award_karma(target.id, str(target), points, str(message.author), reason)
        await message.channel.send(
            f"✅ Awarded **{points} karma** to {target.mention}"
            f"{f' — {reason}' if reason else ''}. New total: **{new_total}**."
        )
        return

    # Anyone can post !export to get the current log as a downloadable file —
    # but only authorized members are allowed to actually receive it
    if message.content.strip().lower() == EXPORT_COMMAND:
        if not isinstance(message.author, discord.Member) or not is_authorized(message.author):
            await message.channel.send(
                f"{message.author.mention} you don't have permission to export "
                "this data. Ask an admin for the "
                f"**{AUTHORIZED_ROLE}** role if you need access."
            )
            return
        if not os.path.exists(LOG_CSV):
            await message.channel.send("No results logged yet.")
            return
        await message.channel.send(
            content=f"{message.author.mention} here's the current results log:",
            file=discord.File(LOG_CSV, filename="discord_results_log.csv"),
        )
        return

    # Only react to images posted with the required tag anywhere in the message
    if REQUIRED_TAG not in message.content.lower():
        return

    image_attachments = [
        a for a in message.attachments if a.filename.lower().endswith(IMAGE_EXTS)
    ]
    if not image_attachments:
        await message.channel.send(
            f"{message.author.mention} I see `{REQUIRED_TAG}` but no image attached — "
            "attach your screenshot in the same message."
        )
        return

    for attachment in image_attachments:
        status_msg = await message.channel.send(f"🔎 Checking `{attachment.filename}`...")

        try:
            image_bytes = await attachment.read()
            # Gemini's SDK call is blocking/synchronous — run it in a thread
            # so it doesn't freeze Discord's event loop (which causes
            # missed heartbeats and disconnects on slower responses).
            result = await asyncio.to_thread(
                process_image_bytes, image_bytes, attachment.filename
            )
        except Exception as e:
            await status_msg.edit(content=f"⚠️ Could not process `{attachment.filename}`: {e}")
            continue

        log_result(str(message.author), str(message.channel), result)
        await status_msg.delete()
        await message.channel.send(
            content=f"{message.author.mention}",
            embed=build_embed(result),
        )

        if result["decision"] == "approved" and isinstance(message.author, discord.Member):
            review_channel = discord.utils.get(
                message.guild.text_channels, name=ADMIN_REVIEW_CHANNEL
            )
            if review_channel is not None:
                await review_channel.send(
                    embed=build_review_embed(result, message.author, attachment),
                    view=ReviewView(message.author.id, str(message.author), result["file"]),
                )
            # if the channel doesn't exist in this server, silently skip —
            # doesn't block the user-facing flow


if __name__ == "__main__":
    client.run(TOKEN)
