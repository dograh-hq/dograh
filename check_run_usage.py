import os
from dotenv import load_dotenv
load_dotenv("d:/Feeding_Trends/calling-saas/dograh/api/.env")

import asyncio, json
from api.routes.main import get_legacy_organization_usage

class FakeUser:
    selected_organization_id = 1

async def main():
    res = await get_legacy_organization_usage(user=FakeUser())
    print("CURRENT ORG 1 USAGE:")
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
