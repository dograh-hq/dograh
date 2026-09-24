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
        res = await session.execute(text("SELECT id, is_completed, usage_info, gathered_context, logs, annotations FROM workflow_runs WHERE id = 127"))
        row = res.fetchone()
        if row:
            print("RUN 127:", dict(row._mapping))

if __name__ == "__main__":
    asyncio.run(check())
