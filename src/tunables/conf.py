from typing import Any

DEFAULTS: dict[str, Any] = {}


class Settings:
    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError


settings = Settings()
