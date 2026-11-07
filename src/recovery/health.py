"""Tiny aiohttp server for the Docker healthcheck.

Liveness, not readiness: it answers 200 whenever the process is up and
reports DB reachability as information only, so a restart of the shared
Postgres container doesn't make Docker restart the bot in a loop.
"""

from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncEngine

from recovery.db.engine import ping


def make_health_app(engine: AsyncEngine) -> web.Application:
    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "db": await ping(engine)})

    app = web.Application()
    app.router.add_get("/health", health)
    return app


async def start_health_server(engine: AsyncEngine, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(make_health_app(engine), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner
