"""Create local Docker MySQL credentials without replacing existing files."""

import os
import secrets
from pathlib import Path


def main():
    path = Path(__file__).resolve().parents[1] / ".env.mysql"
    if path.exists():
        print("Existing .env.mysql preserved.")
        return
    password = secrets.token_hex(24)
    content = (
        f"MYSQL_PASSWORD={password}\n"
        f"MYSQL_ROOT_PASSWORD={secrets.token_hex(32)}\n"
        "MYSQL_PORT=13306\n"
        f"DATABASE_URL=mysql+asyncmy://fitness_bot:{password}@127.0.0.1:13306/fitness_bot"
        "?charset=utf8mb4\n"
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
    print("Created .env.mysql (0600). Credentials were not printed.")


if __name__ == "__main__":
    main()
