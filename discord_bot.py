"""MuLearn General Enablement AI automation, with durable reviews and metrics."""
from __future__ import annotations
import asyncio, json, os, re
from urllib.parse import urlparse
import aiohttp
import discord
from database import audit, award_karma, create_submission, get_profile_muid, initialize, karma_total, pending_submissions, resolve_review, save_profile, set_review_message
from health import start_health_server
from mars_checker import process_general_evidence, process_image_bytes, process_text_evidence
from tasks import task_for_content

TOKEN=os.getenv("DISCORD_BOT_TOKEN","")
if not TOKEN: raise RuntimeError("DISCORD_BOT_TOKEN must be configured.")
AUTHORIZED_ROLE=os.getenv("AUTHORIZED_ROLE","Mars Reviewer"); ADMIN_REVIEW_CHANNEL=os.getenv("ADMIN_REVIEW_CHANNEL","admin-review")
MAX_UPLOAD_MB=int(os.getenv("MAX_UPLOAD_MB","10")); AUTO_AWARD=os.getenv("AUTO_AWARD","true").lower()=="true"; HIGH_RISK_REVIEW=os.getenv("HIGH_RISK_REVIEW","true").lower()=="true"
IMAGE_EXTS=(".png",".jpg",".jpeg",".webp"); intents=discord.Intents.default(); intents.message_content=True
client=discord.Client(intents=intents); health_runner=None

def authorized(member:discord.Member)->bool: return member.guild_permissions.administrator or any(r.name.casefold()==AUTHORIZED_ROLE.casefold() for r in member.roles)
async def public_page_text(url:str)->str:
    """Fetch only a public HTTP(S) page; reject local/private destinations."""
    host=urlparse(url).hostname or ""
    if urlparse(url).scheme not in ("http","https") or host in ("localhost","127.0.0.1","::1") or re.match(r"^(10|127|169\.254|192\.168)\.",host): raise ValueError("Only public HTTP(S) links can be checked.")
    timeout=aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout,headers={"User-Agent":"MuLearnEvidenceBot/1.0"}) as session:
        async with session.get(url,allow_redirects=True) as response:
            if response.status>=400: raise ValueError(f"The link returned HTTP {response.status}.")
            html=await response.content.read(1_000_000)
    return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",html.decode("utf-8","ignore"))).strip()
def result_embed(result:dict,title:str,points:int)->discord.Embed:
    passed=result["decision"]=="approved"; e=discord.Embed(title=("✅ AI verification passed" if passed else "❌ Needs correction")+f" - {title}",color=discord.Color.green() if passed else discord.Color.red())
    e.add_field(name="Karma",value=str(points)); e.add_field(name="AI confidence",value=f"{result.get('confidence','-')}%")
    if passed: e.description="Evidence has been recorded." 
    for n,issue in enumerate(result.get("all_messages","").split(" | "),1):
        if issue: e.add_field(name=f"Result {n}",value=issue,inline=False)
    return e

class ReviewView(discord.ui.View):
    def __init__(self,submission_id:int,user_id:int,filename:str,points:int=100):
        super().__init__(timeout=None); self.submission_id,self.user_id,self.filename,self.points=submission_id,user_id,filename,points
        self.approve.custom_id=f"mulearn:approve:{submission_id}"; self.reject.custom_id=f"mulearn:reject:{submission_id}"
    async def resolve(self,interaction:discord.Interaction,approved:bool):
        if not isinstance(interaction.user,discord.Member) or not authorized(interaction.user): await interaction.response.send_message(f"Reviewer access requires **{AUTHORIZED_ROLE}**.",ephemeral=True); return
        changed,total=await asyncio.to_thread(resolve_review,self.submission_id,interaction.user.id,approved,self.points,str(interaction.user))
        if not changed: await interaction.response.send_message("This submission has already been reviewed.",ephemeral=True); return
        for item in self.children:item.disabled=True
        e=interaction.message.embeds[0]; e.color=discord.Color.green() if approved else discord.Color.red(); e.add_field(name="Review outcome",value=f"{'Approved' if approved else 'Rejected'} by {interaction.user.mention}"+(f"; +{self.points} karma, total {total}" if approved else ""),inline=False)
        await interaction.response.edit_message(embed=e,view=self); await asyncio.to_thread(audit,"review_resolved",f"submission={self.submission_id}; approved={approved}")
    @discord.ui.button(label="Approve",style=discord.ButtonStyle.success,emoji="✅")
    async def approve(self,i:discord.Interaction,_:discord.ui.Button): await self.resolve(i,True)
    @discord.ui.button(label="Reject",style=discord.ButtonStyle.danger,emoji="❌")
    async def reject(self,i:discord.Interaction,_:discord.ui.Button): await self.resolve(i,False)

async def handle_commands(message:discord.Message)->bool:
    text=message.content.strip()
    if text.lower().startswith("!profile"):
        muid=text[8:].strip()
        if not muid: await message.channel.send("Usage: `!profile YOUR-MUID`.")
        else: await asyncio.to_thread(save_profile,message.author.id,muid,str(message.author)); await message.channel.send("✅ Your MuID is saved securely for task verification.")
        return True
    if not text.lower().startswith("!karma"): return False
    if not message.mentions: await message.channel.send("Usage: `!karma @user [points] [reason]`."); return True
    target=message.mentions[0]; rest=re.sub(r"^<@!?\d+>\s*","",text.split(target.mention,1)[-1]).strip()
    if not rest: await message.channel.send(f"{target.mention} has **{await asyncio.to_thread(karma_total,target.id)} karma**."); return True
    if not isinstance(message.author,discord.Member) or not authorized(message.author): await message.channel.send(f"Only **{AUTHORIZED_ROLE}** members can award karma."); return True
    match=re.match(r"([+-]?\d+)\s*(.*)",rest)
    if not match: await message.channel.send("The point value must be an integer."); return True
    total=await asyncio.to_thread(award_karma,target.id,str(target),int(match.group(1)),str(message.author),match.group(2).strip()); await message.channel.send(f"✅ {target.mention}: **{total} karma**."); return True

@client.event
async def on_ready():
    global health_runner
    if health_runner is None: health_runner=await start_health_server()
    for sid,uid,name,points in await asyncio.to_thread(pending_submissions): client.add_view(ReviewView(sid,uid,name,points))
    print(f"Ready as {client.user}; all task automations enabled."); await asyncio.to_thread(audit,"bot_ready",str(client.user))

@client.event
async def on_message(message:discord.Message):
    if message.author.bot or message.guild is None or await handle_commands(message): return
    task=task_for_content(message.content)
    if task is None: return
    attachments=[a for a in message.attachments if a.filename.casefold().endswith(IMAGE_EXTS)]
    links=re.findall(r"https?://[^\s<>]+",message.content)
    if not attachments and not links:
        await message.channel.send(f"{message.author.mention} attach a clear image or add a public evidence link with `{task.hashtags[0]}`. For identity-based tasks, first save your MuID with `!profile YOUR-MUID`."); return
    muid=await asyncio.to_thread(get_profile_muid,message.author.id)
    if not attachments and links:
        url=links[0].rstrip(".,)")
        status=await message.channel.send(f"🔎 AI is reading the public evidence for **{task.title}**...")
        try: result=await asyncio.to_thread(process_text_evidence,url,await public_page_text(url),task.title,task.rules,str(message.author),muid)
        except Exception as exc: await status.edit(content=f"⚠️ I could not verify that public link: {exc}"); return
        row=await asyncio.to_thread(create_submission,attachment_id=f"url:{message.id}",message_id=str(message.id),guild_id=str(message.guild.id),channel_id=str(message.channel.id),user_id=str(message.author.id),user_display=str(message.author),filename=url,decision=result["decision"],review_status="pending" if result["decision"]=="approved" and (task.high_risk and HIGH_RISK_REVIEW or not AUTO_AWARD) else "not_required",result_json=json.dumps({"task":task.code,"points":task.points,"result":result},default=str))
        await status.delete()
        if row is None:return
        await message.channel.send(content=message.author.mention,embed=result_embed(result,task.title,task.points))
        if result["decision"]=="approved" and AUTO_AWARD and not (task.high_risk and HIGH_RISK_REVIEW):
            total=await asyncio.to_thread(award_karma,message.author.id,str(message.author),task.points,"AI automation",f"{task.title} verified automatically"); await message.channel.send(f"✅ **{task.points} karma** awarded automatically. Total: **{total}**.")
        elif result["decision"]=="approved":
            review_channel=discord.utils.get(message.guild.text_channels,name=ADMIN_REVIEW_CHANNEL)
            if review_channel: review=await review_channel.send(embed=result_embed(result,task.title,task.points),view=ReviewView(row.id,message.author.id,url,task.points)); await asyncio.to_thread(set_review_message,row.id,review.id)
        await asyncio.to_thread(audit,"submission_processed",f"task={task.code}; id={row.id}; source=url"); return
    for attachment in attachments:
        if attachment.size>MAX_UPLOAD_MB*1024*1024: await message.channel.send(f"{attachment.filename} exceeds the {MAX_UPLOAD_MB} MB limit."); continue
        status=await message.channel.send(f"🔎 AI is checking **{task.title}**...")
        try:
            data=await attachment.read()
            result=await asyncio.to_thread(process_image_bytes,data,attachment.filename) if task.code=="mars_trek" else await asyncio.to_thread(process_general_evidence,data,attachment.filename,task.title,task.rules,str(message.author),muid)
        except Exception:
            await status.edit(content=f"⚠️ `{attachment.filename}` could not be processed. Retry shortly."); await asyncio.to_thread(audit,"analysis_error",f"task={task.code}; attachment={attachment.id}"); continue
        needs_review=task.high_risk and HIGH_RISK_REVIEW
        row=await asyncio.to_thread(create_submission,attachment_id=str(attachment.id),message_id=str(message.id),guild_id=str(message.guild.id),channel_id=str(message.channel.id),user_id=str(message.author.id),user_display=str(message.author),filename=attachment.filename,decision=result["decision"],review_status="pending" if result["decision"]=="approved" and (needs_review or not AUTO_AWARD) else "not_required",result_json=json.dumps({"task":task.code,"points":task.points,"result":result},default=str))
        await status.delete()
        if row is None: continue
        await message.channel.send(content=message.author.mention,embed=result_embed(result,task.title,task.points))
        if result["decision"]!="approved": await asyncio.to_thread(audit,"submission_rejected",f"task={task.code}; id={row.id}"); continue
        if AUTO_AWARD and not needs_review:
            total=await asyncio.to_thread(award_karma,message.author.id,str(message.author),task.points,"AI automation",f"{task.title} verified automatically")
            await message.channel.send(f"✅ {message.author.mention}, **{task.points} karma** has been awarded automatically. Total: **{total}**.")
        else:
            review_channel=discord.utils.get(message.guild.text_channels,name=ADMIN_REVIEW_CHANNEL)
            if review_channel is None: await message.channel.send("⚠️ Passed AI checks, but the reviewer queue is not configured.")
            else:
                review=await review_channel.send(embed=result_embed(result,task.title,task.points),view=ReviewView(row.id,message.author.id,attachment.filename,task.points)); await asyncio.to_thread(set_review_message,row.id,review.id)
        await asyncio.to_thread(audit,"submission_processed",f"task={task.code}; id={row.id}; decision=approved")

if __name__=="__main__": initialize(); client.run(TOKEN,log_handler=None)
