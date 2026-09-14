import os
from dotenv import load_dotenv
load_dotenv("d:/Feeding_Trends/calling-saas/dograh/api/.env")

import asyncio
import json
from sqlalchemy import text
from api.db import db_client

async def main():
    async with db_client.async_session() as session:
        res = await session.execute(text("SELECT id, is_completed, state, usage_info FROM workflow_runs WHERE id = 92"))
        row = res.fetchone()
        print("Run 92 usage_info:")
        if row and row[3]:
            print(json.dumps(row[3], indent=2))
        else:
            print(row)

if __name__ == "__main__":
    asyncio.run(main())
