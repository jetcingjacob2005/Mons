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
attachment (.png/.jpg/.jpeg/.webp), the bot downloads it in memory,
runs it through the same classifier as the batch script, replies in
the channel with an approved/rejected embed + reasons, and appends
the result to discord_results_log.csv for your records.
"""
import discord
import os
import csv
from datetime import datetime, timezone

from mars_checker import process_image_bytes

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
if not TOKEN:
    raise RuntimeError(
        "No Discord bot token found. Set the DISCORD_BOT_TOKEN environment "
        "variable before running this script."
    )

LOG_CSV    = "discord_results_log.csv"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

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


def build_embed(result: dict) -> discord.Embed:
    approved = result["decision"] == "approved"
    embed = discord.Embed(
        title=f"{'✅ Approved' if approved else '❌ Rejected'} — {result['file']}",
        color=discord.Color.green() if approved else discord.Color.red(),
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

    for attachment in message.attachments:
        if not attachment.filename.lower().endswith(IMAGE_EXTS):
            continue

        status_msg = await message.channel.send(f"🔎 Checking `{attachment.filename}`...")

        try:
            image_bytes = await attachment.read()
            result = process_image_bytes(image_bytes, attachment.filename)
        except Exception as e:
            await status_msg.edit(content=f"⚠️ Could not process `{attachment.filename}`: {e}")
            continue

        log_result(str(message.author), str(message.channel), result)
        await status_msg.delete()
        await message.channel.send(
            content=f"{message.author.mention}",
            embed=build_embed(result),
        )


if __name__ == "__main__":
    client.run(TOKEN)
