# Dograh SDK Examples

Runnable examples of the Dograh SDK in Python and TypeScript.

## Shared environment variables

Copy `.env.example` to `.env` in each example directory and fill in your values, then `source .env` (or use your preferred dotenv loader).

| Variable              | Description                                                  |
| --------------------- | ------------------------------------------------------------ |
| `DOGRAH_API_ENDPOINT` | Dograh API base URL (e.g. `http://localhost:8000`)           |
| `DOGRAH_API_TOKEN`    | API token — sent as `X-API-Key`                              |

The workflow ID and destination phone number are set as constants at the top of each example script — edit them there.

## Python

```bash
pip install dograh-sdk

export DOGRAH_API_ENDPOINT=http://localhost:8000
export DOGRAH_API_TOKEN=sk-...

# Fetch a workflow by ID and place a test phone call.
python python/fetch_workflow_and_call.py

# Create a new workflow from a definition.
python python/create_workflow.py
```

## TypeScript

Uses `tsx` to run directly.

```bash
cd typescript
npm install

export DOGRAH_API_ENDPOINT=http://localhost:8000
export DOGRAH_API_TOKEN=sk-...

npm run call    # fetch_workflow_and_call.ts
npm run create  # create_workflow.ts
```
