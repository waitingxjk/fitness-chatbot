import uvicorn

from fitness_bot.config import Settings


def main():
    settings = Settings()
    uvicorn.run("fitness_bot.app:create_app", factory=True, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
