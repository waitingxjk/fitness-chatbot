"""Administrator-only CLI: issue a secret once, or revoke by non-secret token ID."""

import argparse
import asyncio
import json
from datetime import timedelta

from fitness_bot.config import Settings
from fitness_bot.identity import IdentityStore, utcnow, valid_user_id
from fitness_bot.storage import ConversationStore


async def run(args):
    settings = Settings()
    if not settings.database_url:
        raise ValueError("DATABASE_URL is required")
    store = ConversationStore(settings.database_url.get_secret_value())
    try:
        await store.initialize(settings.default_user_id, settings.image_directory)
        identity = IdentityStore(store)
        if args.command == "create-user":
            return await identity.create_user(args.display_name)
        if args.command == "issue":
            expiry = (
                utcnow() + timedelta(days=args.expires_in_days) if args.expires_in_days else None
            )
            return await identity.issue_token(args.user_id, expiry)
        revoked = await identity.revoke_token(args.token_id)
        if not revoked:
            raise ValueError("Token ID not found")
        return {"tokenId": args.token_id, "revoked": True}
    finally:
        await store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user", help="Create one server-generated user")
    create.add_argument("--display-name")
    issue = sub.add_parser("issue", help="Print a new Bearer token once; protect this output")
    issue.add_argument("--user-id", required=True, type=valid_user_id)
    issue.add_argument("--expires-in-days", type=float)
    revoke = sub.add_parser("revoke")
    revoke.add_argument("--token-id", required=True)
    args = parser.parse_args()
    if (
        args.command == "issue"
        and args.expires_in_days is not None
        and not 0 < args.expires_in_days <= 36500
    ):
        parser.error("--expires-in-days must be positive and at most 36500")
    try:
        result = asyncio.run(run(args))
    except ValueError as exc:
        parser.error(str(exc))
    except Exception:
        # Connection exceptions may include sensitive configuration; do not print them.
        parser.exit(1, "Token operation failed; check database availability and configuration.\n")
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
