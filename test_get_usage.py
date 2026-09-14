import os
from dotenv import load_dotenv
load_dotenv("d:/Feeding_Trends/calling-saas/dograh/api/.env")

import asyncio
from api.db import db_client

async def main():
    res = await db_client.get_usage_history(2)
    runs, total_count, total_tokens, total_duration = res
    print("Total count:", total_count)
    print("Total duration:", total_duration)
    print("Total runs returned:", len(runs))
    for r in runs[:5]:
        print(f"ID: {r['id']} | Agent: {r['workflow_name']} | Duration: {r['call_duration_seconds']} | Cost: {r.get('charge_usd')}")

if __name__ == "__main__":
    asyncio.run(main())
