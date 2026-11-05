from aiogram import Dispatcher

from cardlegends.application.players import PlayerService
from cardlegends.bot.handlers import create_router
from cardlegends.bot.middleware import ServicesMiddleware


def create_dispatcher(players: PlayerService) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(ServicesMiddleware(players))
    dispatcher.include_router(create_router())
    return dispatcher
