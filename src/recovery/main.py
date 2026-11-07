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
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from recovery.bot_factory import build_dispatcher
from recovery.config import get_settings
from recovery.db.engine import make_engine, make_session_factory, wait_for_db
from recovery.domain import PromptKind
from recovery.health import start_health_server
from recovery.logging_setup import setup_logging
from recovery.scheduler.service import CheckinScheduler
from recovery.telegram.routers.reports import send_session_brief

logger = logging.getLogger("recovery")

BOT_COMMANDS = [
    BotCommand(command="morning", description="Утренний опрос"),
    BotCommand(command="evening", description="Вечерний опрос"),
    BotCommand(command="now", description="Отметить состояние сейчас"),
    BotCommand(command="discuss", description="Добавить пункт для сессии"),
    BotCommand(command="session", description="Сводка к сессии"),
    BotCommand(command="week", description="Записи за неделю (файл)"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="privacy", description="Данные и приватность"),
    BotCommand(command="help", description="Подсказка"),
]


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
    session_factory = make_session_factory(engine)
    scheduler = CheckinScheduler(
        AsyncIOScheduler(timezone="UTC"),
        bot,
        session_factory,
        reminder_delay_minutes=settings.reminder_delay_minutes,
        catchup_window_minutes=settings.catchup_window_minutes,
        extra_senders={PromptKind.SESSION_BRIEF: send_session_brief},
    )
    dp = build_dispatcher(
        owner_id=settings.owner_user_id,
        session_factory=session_factory,
        default_timezone=settings.default_timezone,
        settings_changed=scheduler.reschedule,
        user_deleted=scheduler.forget,
        emergency_number=settings.emergency_number,
    )
    try:
        await ensure_polling_is_safe(bot)
        await bot.set_my_commands(BOT_COMMANDS)
        await scheduler.start()
        logger.info("starting polling")
        # One owner, so concurrency buys nothing — and processing updates one
        # at a time keeps them in order and rules out races between them
        # (a burst queued while the bot was down, a double-tapped button).
        await dp.start_polling(bot, handle_as_tasks=False)
    finally:
        if scheduler.scheduler.running:
            scheduler.scheduler.shutdown(wait=False)
        await bot.session.close()
        await health.cleanup()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
