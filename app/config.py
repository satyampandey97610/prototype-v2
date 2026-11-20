import os
from functools import lru_cache
from pydantic import BaseModel, ConfigDict

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


class Settings(BaseModel):
    model_config = ConfigDict(extra="allow")

    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://microheal:microheal@localhost:5432/microheal",
    )
    SYNC_DATABASE_URL: str = os.getenv(
        "SYNC_DATABASE_URL",
        "postgresql+psycopg2://microheal:microheal@localhost:5432/microheal",
    )
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "1536"))

    @property
    def database_url(self) -> str:
        return self.DATABASE_URL

    @property
    def sync_database_url(self) -> str:
        return self.SYNC_DATABASE_URL

    @property
    def openai_api_key(self) -> str:
        return self.OPENAI_API_KEY

    @property
    def embedding_dim(self) -> int:
        return self.EMBEDDING_DIM


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        DATABASE_URL=os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://microheal:microheal@localhost:5432/microheal",
        ),
        SYNC_DATABASE_URL=os.getenv(
            "SYNC_DATABASE_URL",
            "postgresql+psycopg2://microheal:microheal@localhost:5432/microheal",
        ),
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
        EMBEDDING_DIM=int(os.getenv("EMBEDDING_DIM", "1536")),
    )


settings = get_settings()