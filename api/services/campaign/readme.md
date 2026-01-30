### campaign_orchestrator.py (CampaignOrchestrator)

- Listens to retry events, batch completed event, sync completed events from redis pubsub, and schedules batches
- Monitors stale campaigns and schedules batches if one is not already scheduled
- Marks campaign as completed if no more tasks pending

### runner.py (CampaignRunnerService)

- Service layer to handle router requests, like run campaign, pause campaign, resume campaign, get campaign status etc.

### call_dispatcher.py (CampaignCallDispatcher)

- Ensures rate limit and concurrency limits and dispatches call using telephony provider

### campaign_tasks.py

- sync campaign from source
- process campaign batch
