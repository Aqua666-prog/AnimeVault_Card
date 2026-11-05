from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def create_database(url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"timeout": 5, "command_timeout": 5},
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)
