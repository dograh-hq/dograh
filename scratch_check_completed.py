import os
import sys

base_dir = os.path.dirname(os.path.abspath(__file__))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

from dotenv import load_dotenv
load_dotenv(os.path.join(base_dir, "api", ".env"))

import asyncio
from api.db import db_client
from sqlalchemy import text

async def check():
    async with db_client.async_session() as session:
        res = await session.execute(text("SELECT id, is_completed, usage_info, gathered_context, annotations FROM workflow_runs WHERE (usage_info->>'call_duration_seconds')::int > 0 ORDER BY id DESC LIMIT 5"))
        print("=== RUNS WITH DURATION > 0 ===")
        for r in res.fetchall():
            row = dict(r._mapping)
            print(f"ID: {row['id']}")
            print(f"  usage_info: {row['usage_info']}")
            print(f"  gathered_context: {row['gathered_context']}")
            print(f"  annotations: {row['annotations']}")

if __name__ == "__main__":
    asyncio.run(check())
