import os
import sys

base_dir = os.path.dirname(os.path.abspath(__file__))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

from dotenv import load_dotenv
load_dotenv(os.path.join(base_dir, "api", ".env"))

import asyncio
from api.db import db_client
from api.routes.campaign import get_campaign_run_detail
from api.db.models import UserModel

async def test():
    user = UserModel(id=1, selected_organization_id=1, is_superuser=True)
    detail = await get_campaign_run_detail(136, user)
    print("recording_url:", detail.get("recording_url"))
    print("extracted_data keys:", list(detail.get("extracted_data", {}).keys()))
    print("transcript_turns count:", len(detail.get("transcript_turns", [])))

if __name__ == "__main__":
    asyncio.run(test())
