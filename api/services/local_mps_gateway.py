"""
Local MPS Gateway — drop-in replacement for Dograh MPS cloud service.

Combines workflow generation + document processing into a single local service.
Point MPS_API_URL at this and Dograh works fully offline.

Run:
    python -m api.services.local_mps_gateway

Configure:
    MPS_API_URL=http://localhost:9000
"""

from __future__ import annotations

import os
import uvicorn
from fastapi import FastAPI

app = FastAPI(title="Local MPS Gateway (Workflow + Documents)")

# Import and register routes from sub-modules
from local_mps_workflow_generator import generate_workflow, CreateWorkflowRequest as WFRequest
from local_mps_doc_processor import process_document as doc_process, search_documents as doc_search, UploadFile, File, Form

# ── Workflow routes ──
@app.post("/api/v1/workflow/create-workflow")
async def create_workflow(request: WFRequest):
    return generate_workflow(
        call_type=request.call_type,
        use_case=request.use_case,
        activity_description=request.activity_description,
        language=request.language,
    )

# ── Document routes ──
@app.post("/api/v1/document/process")
async def process_document(
    file: UploadFile = File(...),
    retrieval_mode: str = Form("full_document"),
    document_uuid: str = Form(""),
    s3_key: str = Form(""),
):
    return await doc_process(file, retrieval_mode, document_uuid, s3_key)

@app.post("/api/v1/document/search")
async def search_documents(
    query: str = Form(...),
    document_uuid: str = Form(""),
    limit: int = Form(5),
):
    return await doc_search(query, document_uuid, limit)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "local-mps-gateway", "modules": ["workflow", "document"]}


if __name__ == "__main__":
    port = int(os.getenv("LOCAL_MPS_PORT", "9000"))
    print(f"Starting Local MPS Gateway on port {port}")
    print(f"  Workflow generator:  POST /api/v1/workflow/create-workflow")
    print(f"  Document processor:  POST /api/v1/document/process")
    print(f"  Document search:     POST /api/v1/document/search")
    print(f"Configure Dograh with: MPS_API_URL=http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
