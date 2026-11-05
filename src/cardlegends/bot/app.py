from aiogram import Dispatcher

from cardlegends.application.players import PlayerService
from cardlegends.bot.handlers import create_router
from cardlegends.bot.middleware import ServicesMiddleware


def create_dispatcher(players: PlayerService, game=None) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(ServicesMiddleware(players))
    if game is not None:
        from cardlegends.bot.game_ui import create_game_router

        dispatcher.include_router(create_game_router(game, players))
    else:
        dispatcher.include_router(create_router())
    return dispatcher
