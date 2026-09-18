"""Loopback-only server for synthetic Flutter upload integration tests."""

from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from fitness_bot.app import create_app
from fitness_bot.config import Settings

if __name__ == "__main__":
    with TemporaryDirectory(prefix="workout-http-") as directory:
        settings = Settings(
            _env_file=None,
            database_url=f"sqlite+aiosqlite:///{directory}/test.db",
            openai_api_key=None,
            image_directory=Path(directory) / "uploads",
        )
        uvicorn.run(create_app(settings), host="127.0.0.1", port=18081)
