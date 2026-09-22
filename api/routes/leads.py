import json
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import text

from api.db import db_client

router = APIRouter(
    prefix="/leads",
    tags=["leads"],
)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS leads (
    id SERIAL PRIMARY KEY,
    kind VARCHAR(50) NOT NULL,
    name VARCHAR(255),
    company VARCHAR(255),
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(100),
    job_title VARCHAR(255),
    volume VARCHAR(100),
    deployment VARCHAR(100),
    agent_goal TEXT,
    source VARCHAR(100),
    origin VARCHAR(100),
    country VARCHAR(100),
    timezone VARCHAR(100),
    status VARCHAR(50) DEFAULT 'new',
    notes TEXT DEFAULT '',
    raw_payload JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

ALTER_TABLE_STATEMENTS = [
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'new'",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT ''",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS raw_payload JSONB DEFAULT '{}'::jsonb",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()",
]


async def ensure_table():
    try:
        async with db_client.async_session() as session:
            await session.execute(text(CREATE_TABLE_SQL))
            for sql in ALTER_TABLE_STATEMENTS:
                try:
                    await session.execute(text(sql))
                except Exception as ex:
                    logger.debug(f"Column migration check note: {ex}")
            await session.commit()
    except Exception as e:
        logger.warning(f"Could not auto-create or alter leads table: {e}")


async def save_lead(kind: str, data: Dict[str, Any]) -> int:
    await ensure_table()
    async with db_client.async_session() as session:
        query = text("""
            INSERT INTO leads (
                kind, name, company, email, phone, job_title, volume,
                deployment, agent_goal, source, origin, country, timezone,
                status, notes, raw_payload
            ) VALUES (
                :kind, :name, :company, :email, :phone, :job_title, :volume,
                :deployment, :agent_goal, :source, :origin, :country, :timezone,
                'new', '', :raw_payload
            ) RETURNING id;
        """)
        result = await session.execute(
            query,
            {
                "kind": kind,
                "name": data.get("name") or "",
                "company": data.get("company") or "",
                "email": data.get("email") or data.get("workEmail") or "",
                "phone": data.get("phone") or "",
                "job_title": data.get("jobTitle") or "",
                "volume": data.get("volume") or "",
                "deployment": data.get("deployment") or "",
                "agent_goal": data.get("agentGoal") or "",
                "source": data.get("source") or "",
                "origin": data.get("origin") or "",
                "country": data.get("country") or "",
                "timezone": data.get("timezone") or "",
                "raw_payload": json.dumps(data),
            },
        )
        await session.commit()
        row = result.fetchone()
        return row[0] if row else 0


@router.post("/enterprise")
async def enterprise_lead(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    name = body.get("name", "")
    email = body.get("workEmail") or body.get("email", "")
    company = body.get("company", "")
    phone = body.get("phone", "")
    volume = body.get("volume", "")
    goal = body.get("agentGoal", "")

    logger.info(
        f"[NEW STRATEGY CALL / ENTERPRISE ENQUIRY] Name='{name}', Company='{company}', Email='{email}', Phone='{phone}', Volume='{volume}', Goal='{goal}'"
    )

    lead_id = await save_lead("enterprise", body)
    return {
        "ok": True,
        "lead_id": lead_id,
        "show_calendar": False,
        "message": "Enquiry received successfully",
    }


@router.post("/hire-expert")
async def hire_expert_lead(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    name = body.get("name", "")
    email = body.get("email", "")
    company = body.get("company", "")
    phone = body.get("phone", "")
    volume = body.get("volume", "")
    goal = body.get("agentGoal", "")

    logger.info(
        f"[NEW DONE-FOR-YOU ENQUIRY] Name='{name}', Company='{company}', Email='{email}', Phone='{phone}', Volume='{volume}', Goal='{goal}'"
    )

    lead_id = await save_lead("hire_expert", body)
    return {
        "ok": True,
        "lead_id": lead_id,
        "show_calendar": False,
        "message": "Enquiry received successfully",
    }


class UpdateLeadRequest(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


@router.get("")
async def list_leads(
    limit: int = Query(100, ge=1, le=500),
    kind: Optional[str] = Query(None, description="Filter by kind: hire_expert or enterprise"),
    status: Optional[str] = Query(None, description="Filter by status: new, contacted, in_discussion, qualified, closed"),
    search: Optional[str] = Query(None, description="Search by name, email, company, or phone"),
):
    """List enquiries / leads saved in PostgreSQL with superadmin stats."""
    await ensure_table()
    async with db_client.async_session() as session:
        # Build query dynamically
        where_clauses = ["1=1"]
        params: Dict[str, Any] = {"limit": limit}

        if kind:
            where_clauses.append("kind = :kind")
            params["kind"] = kind

        if status:
            where_clauses.append("status = :status")
            params["status"] = status

        if search:
            where_clauses.append(
                "(name ILIKE :search OR email ILIKE :search OR company ILIKE :search OR phone ILIKE :search OR agent_goal ILIKE :search)"
            )
            params["search"] = f"%{search}%"

        where_sql = " AND ".join(where_clauses)
        query = text(f"""
            SELECT id, kind, name, company, email, phone, job_title, volume,
                   deployment, agent_goal, source, origin, country, timezone,
                   COALESCE(status, 'new') as status, COALESCE(notes, '') as notes,
                   raw_payload, created_at
            FROM leads
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT :limit;
        """)
        result = await session.execute(query, params)
        rows = result.fetchall()

        leads = []
        for r in rows:
            raw_payload_data = {}
            if r[16]:
                try:
                    raw_payload_data = json.loads(r[16]) if isinstance(r[16], str) else r[16]
                except Exception:
                    raw_payload_data = {}

            leads.append({
                "id": r[0],
                "kind": r[1],
                "name": r[2] or "",
                "company": r[3] or "",
                "email": r[4] or "",
                "phone": r[5] or "",
                "job_title": r[6] or "",
                "volume": r[7] or "",
                "deployment": r[8] or "",
                "agent_goal": r[9] or "",
                "source": r[10] or "",
                "origin": r[11] or "",
                "country": r[12] or "",
                "timezone": r[13] or "",
                "status": r[14] or "new",
                "notes": r[15] or "",
                "raw_payload": raw_payload_data,
                "created_at": r[17].isoformat() if r[17] else None,
            })

        # Overall summary counts for superadmin badges and KPI cards
        counts_res = await session.execute(text("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE kind = 'hire_expert') as hire_expert_count,
                COUNT(*) FILTER (WHERE kind = 'enterprise') as enterprise_count,
                COUNT(*) FILTER (WHERE COALESCE(status, 'new') = 'new') as new_count,
                COUNT(*) FILTER (WHERE status = 'contacted') as contacted_count,
                COUNT(*) FILTER (WHERE status = 'qualified') as qualified_count
            FROM leads;
        """))
        c_row = counts_res.fetchone()

        stats = {
            "total": c_row[0] if c_row else len(leads),
            "hire_expert_count": c_row[1] if c_row else 0,
            "enterprise_count": c_row[2] if c_row else 0,
            "new_count": c_row[3] if c_row else 0,
            "contacted_count": c_row[4] if c_row else 0,
            "qualified_count": c_row[5] if c_row else 0,
        }

        return {
            "stats": stats,
            "total": len(leads),
            "leads": leads,
        }


@router.patch("/{lead_id}")
async def update_lead(lead_id: int, request: UpdateLeadRequest):
    """Update lead status or internal notes."""
    await ensure_table()
    async with db_client.async_session() as session:
        updates = []
        params: Dict[str, Any] = {"id": lead_id}

        if request.status is not None:
            updates.append("status = :status")
            params["status"] = request.status

        if request.notes is not None:
            updates.append("notes = :notes")
            params["notes"] = request.notes

        if not updates:
            raise HTTPException(status_code=400, detail="No fields provided to update")

        sql = f"UPDATE leads SET {', '.join(updates)} WHERE id = :id RETURNING id;"
        res = await session.execute(text(sql), params)
        await session.commit()
        row = res.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Lead not found")

        return {"ok": True, "lead_id": lead_id, "message": "Lead updated successfully"}


@router.delete("/{lead_id}")
async def delete_lead(lead_id: int):
    """Delete a lead enquiry."""
    await ensure_table()
    async with db_client.async_session() as session:
        res = await session.execute(text("DELETE FROM leads WHERE id = :id RETURNING id;"), {"id": lead_id})
        await session.commit()
        row = res.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Lead not found")

        return {"ok": True, "lead_id": lead_id, "message": "Lead deleted successfully"}
