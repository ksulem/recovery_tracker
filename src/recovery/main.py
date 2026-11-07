"""Entrypoint: long polling + health endpoint.

Long polling rather than a webhook: the bot needs no inbound port, no nginx
route and no certificate, so it stays fully independent of the
watering-system stack it shares a VM with.
"""

import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from recovery.bot_factory import build_dispatcher
from recovery.config import get_settings
from recovery.db.engine import make_engine, wait_for_db
from recovery.health import start_health_server
from recovery.logging_setup import setup_logging

logger = logging.getLogger("recovery")


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    engine = make_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    health = await start_health_server(engine, settings.health_host, settings.health_port)
    await wait_for_db(engine)

    bot = Bot(
        settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher(owner_id=settings.owner_user_id)
    try:
        logger.info("starting polling")
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await health.cleanup()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
