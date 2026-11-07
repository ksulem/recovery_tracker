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


class ForeignWebhookError(RuntimeError):
    pass


async def ensure_polling_is_safe(bot: Bot) -> None:
    """Refuse to start if this token has a webhook configured.

    This bot never sets a webhook, so one being present means the token
    belongs to another bot — most likely the watering bot, which runs on a
    webhook on the same VM. Deleting it (what polling would require) would
    silently break that bot, so we stop instead.
    """
    info = await bot.get_webhook_info()
    if info.url:
        raise ForeignWebhookError(
            "RECOVERY_BOT_TOKEN has a webhook configured, so it is probably another "
            "bot's token (e.g. the watering bot). Refusing to start; the webhook was "
            "left untouched. Use the token of the recovery bot from @BotFather."
        )


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
        await ensure_polling_is_safe(bot)
        logger.info("starting polling")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await health.cleanup()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
