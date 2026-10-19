# NASA Mons Discord Automation

A deployable Discord worker for the MuLearn General Enablement tasks. It validates tagged screenshots with Gemini, stores all submissions in PostgreSQL, awards verified low-risk tasks automatically, routes high-risk work to reviewers, and provides `/health` monitoring for Railway.

## Free deployment: Koyeb + Neon

1. Create a free PostgreSQL project at [Neon](https://neon.com/), then copy its **pooled** connection string. It must include `sslmode=require`.
2. Push this folder to GitHub. In [Koyeb](https://app.koyeb.com/), create a **Web Service** from that repository. Koyeb detects the included `Dockerfile` automatically.
3. Select Koyeb's `free` instance, port `8080`, and health-check path `/health`.
4. Add these Koyeb environment variables: `DISCORD_BOT_TOKEN`, `GEMINI_API_KEY`, and `DATABASE_URL` (the Neon connection string). Add the remaining optional values from `.env.example` if you want to change roles, limits, or automation policy.
5. In Discord Developer Portal, enable **Message Content Intent**, then invite the bot with View Channels, Send Messages, Embed Links, Read Message History, and Attach Files.
6. Create `#admin-review` and the `Mars Reviewer` role (or update the matching variables), then deploy. The service creates its database tables automatically on first boot.

Supported hashtags include Discord Guide, Qseverse, Self Intro, Find Better, all four Typing Challenge levels, Life Goals, NASA Mons, both 30 Days of Coding milestones, Aami's Story, Drug Free Kerala, Change the Look, both Snakify milestones, Fail Forward, My Blog, and Speaking/Listening. Members can submit a screenshot or a public evidence link where the task calls for it. Use `!profile YOUR-MUID` once before identity-linked tasks. Decisions, audit events, karma, and duplicate protection are database-backed. Pending review buttons are restored after a restart.

`AUTO_AWARD=true` automatically awards passed low-risk tasks. Keep `HIGH_RISK_REVIEW=true` for wallet, presentation, and Figma evidence: these pass AI checks but must be approved in `#admin-review`, which prevents easy impersonation and stolen-work fraud.

`!karma @member` checks balance. Reviewers can run `!karma @member 25 helpful-review`.

For local development, run `pip install -r requirements.txt`, configure the two tokens, then `python discord_bot.py`. Without `DATABASE_URL`, it uses local `mons_bot.db`.
