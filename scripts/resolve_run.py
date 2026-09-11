"""Operator command: resolve unknown only AFTER checking the old execution stopped."""

import argparse
import asyncio

from app.config import Settings
from app.storage.jobs import JobRepository


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--confirm-execution-stopped", action="store_true", required=True)
    args = parser.parse_args()
    settings = Settings()
    repository = JobRepository(settings.database_path, settings.max_records)
    await repository.initialize()
    run = await repository.resolve_unknown(args.run_id)
    print(run.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
