import os
from dotenv import load_dotenv
load_dotenv("d:/Feeding_Trends/calling-saas/dograh/api/.env")

import asyncio
from api.db import db_client
from api.services.workflow.run_usage_response import format_public_cost_info, format_public_usage_info

async def main():
    run = await db_client.get_workflow_run(92)
    print("run.cost_info:", run.cost_info)
    print("run.usage_info:", run.usage_info)
    print("formatted cost_info:", format_public_cost_info(run.cost_info, run.usage_info))
    print("formatted usage_info:", format_public_usage_info(run.usage_info))

if __name__ == "__main__":
    asyncio.run(main())
