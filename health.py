import os
from aiohttp import web
from database import metrics
async def health(_: web.Request) -> web.Response: return web.json_response({"status":"ok","service":"mons-discord-automation",**metrics()})
async def start_health_server() -> web.AppRunner:
    app=web.Application(); app.router.add_get("/health",health); app.router.add_get("/",health)
    runner=web.AppRunner(app); await runner.setup(); await web.TCPSite(runner,"0.0.0.0",int(os.getenv("PORT","8080"))).start(); return runner
