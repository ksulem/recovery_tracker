from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message


async def ping(message: Message) -> None:
    await message.answer("pong")


def make_router() -> Router:
    # Routers are built per dispatcher (a Router can only be attached once),
    # so tests can create as many dispatchers as they need.
    router = Router(name="common")
    router.message.register(ping, Command("ping"))
    return router
