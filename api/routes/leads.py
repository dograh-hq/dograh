import json
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import text

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user

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
    organization_id INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

ALTER_TABLE_STATEMENTS = [
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'new'",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT ''",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS raw_payload JSONB DEFAULT '{}'::jsonb",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS organization_id INTEGER",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS is_converted BOOLEAN DEFAULT FALSE",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS conversion_reason TEXT DEFAULT ''",
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


async def get_optional_auth_user(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> Optional[UserModel]:
    if not authorization and not x_api_key:
        return None
    try:
        return await get_user(authorization, x_api_key)
    except Exception:
        return None


async def save_lead(
    kind: str, data: Dict[str, Any], organization_id: Optional[int] = None
) -> int:
    await ensure_table()
    async with db_client.async_session() as session:
        query = text("""
            INSERT INTO leads (
                kind, name, company, email, phone, job_title, volume,
                deployment, agent_goal, source, origin, country, timezone,
                status, notes, raw_payload, organization_id
            ) VALUES (
                :kind, :name, :company, :email, :phone, :job_title, :volume,
                :deployment, :agent_goal, :source, :origin, :country, :timezone,
                'new', '', :raw_payload, :organization_id
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
                "organization_id": organization_id,
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


class CreateLeadRequest(BaseModel):
    name: str
    phone: str
    email: Optional[str] = ""
    company: Optional[str] = ""
    job_title: Optional[str] = ""
    tags: Optional[List[str]] = []
    status: Optional[str] = "new"
    notes: Optional[str] = ""
    kind: Optional[str] = "contact"
    agent_goal: Optional[str] = ""


class UpdateLeadRequest(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    tags: Optional[List[str]] = None


@router.post("")
@router.post("/")
async def create_lead(
    request: CreateLeadRequest,
    user: Optional[UserModel] = Depends(get_optional_auth_user),
):
    """Create a new contact / lead in PostgreSQL scoped to workspace."""
    payload = {
        "name": request.name,
        "phone": request.phone,
        "email": request.email or "",
        "company": request.company or "",
        "jobTitle": request.job_title or "",
        "status": request.status or "new",
        "notes": request.notes or "",
        "tags": request.tags or [],
        "agentGoal": request.agent_goal or "",
    }
    org_id = user.selected_organization_id if user else None
    lead_id = await save_lead(request.kind or "contact", payload, organization_id=org_id)
    return {
        "ok": True,
        "lead_id": lead_id,
        "organization_id": org_id,
        "message": "Contact created successfully",
    }


@router.get("")
@router.get("/")
async def list_leads(
    limit: int = Query(100, ge=1, le=500),
    kind: Optional[str] = Query(None, description="Filter by kind: hire_expert or enterprise"),
    status: Optional[str] = Query(None, description="Filter by status: new, contacted, in_discussion, qualified, closed"),
    search: Optional[str] = Query(None, description="Search by name, email, company, or phone"),
    organization_id: Optional[int] = Query(None, description="Filter explicitly by workspace/organization ID"),
    user: Optional[UserModel] = Depends(get_optional_auth_user),
):
    """List enquiries / leads saved in PostgreSQL scoped to current workspace."""
    await ensure_table()
    async with db_client.async_session() as session:
        # Build query dynamically
        where_clauses = ["1=1"]
        params: Dict[str, Any] = {"limit": limit}

        # Workspace isolation: filter by current organization or shared unassigned leads
        target_org_id = organization_id or (user.selected_organization_id if user else None)
        if target_org_id is not None:
            where_clauses.append("(organization_id = :org_id OR organization_id IS NULL)")
            params["org_id"] = target_org_id

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
                   raw_payload, created_at, organization_id
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
                "organization_id": r[18],
            })

        # Seed initial realistic leads if database has none
        if not leads and not search:
            default_leads = [
                {
                    "name": "Rahul Sharma",
                    "company": "UrbanNest Realty",
                    "email": "rahul.sharma@example.com",
                    "phone": "+919876543210",
                    "city": "Gurgaon",
                    "status": "new",
                    "agent_goal": "Schedule property site visit for 3 BHK",
                },
                {
                    "name": "Priya Verma",
                    "company": "Verma Healthcare",
                    "email": "priya.verma@example.com",
                    "phone": "+919123456789",
                    "city": "Noida",
                    "status": "contacted",
                    "agent_goal": "Doctor consultation slot confirmation",
                },
                {
                    "name": "Amit Patel",
                    "company": "Apex Solar Tech",
                    "email": "amit.patel@example.com",
                    "phone": "+919822334455",
                    "city": "Ahmedabad",
                    "status": "qualified",
                    "agent_goal": "Rooftop solar commercial quotation",
                },
                {
                    "name": "Sneha Iyer",
                    "company": "SwiftStore D2C",
                    "email": "sneha.iyer@example.com",
                    "phone": "+919765432109",
                    "city": "Bangalore",
                    "status": "new",
                    "agent_goal": "COD order address verification before shipping",
                },
                {
                    "name": "Vikram Singh",
                    "company": "Royal Auto Hub",
                    "email": "vikram.singh@example.com",
                    "phone": "+919811223344",
                    "city": "Delhi",
                    "status": "in_discussion",
                    "agent_goal": "Test drive booking for SUV",
                },
            ]
            for dl in default_leads:
                await save_lead("contact", dl, organization_id=target_org_id)
            result = await session.execute(query, params)
            rows = result.fetchall()
            for r in rows:
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
                    "raw_payload": {},
                    "created_at": r[17].isoformat() if r[17] else None,
                    "organization_id": r[18],
                })

        # Overall summary counts for superadmin badges and KPI cards scoped to workspace
        counts_sql = f"""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE kind = 'hire_expert') as hire_expert_count,
                COUNT(*) FILTER (WHERE kind = 'enterprise') as enterprise_count,
                COUNT(*) FILTER (WHERE COALESCE(status, 'new') = 'new') as new_count,
                COUNT(*) FILTER (WHERE status = 'contacted') as contacted_count,
                COUNT(*) FILTER (WHERE status = 'qualified') as qualified_count
            FROM leads
            WHERE {where_sql};
        """
        counts_res = await session.execute(text(counts_sql), {k: v for k, v in params.items() if k != "limit"})
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
            "organization_id": target_org_id,
        }


@router.patch("/{lead_id}")
async def update_lead(
    lead_id: int,
    request: UpdateLeadRequest,
    user: Optional[UserModel] = Depends(get_optional_auth_user),
):
    """Update lead status or internal notes scoped to current workspace."""
    await ensure_table()
    async with db_client.async_session() as session:
        if user and user.selected_organization_id:
            check = await session.execute(
                text("SELECT organization_id FROM leads WHERE id = :id"), {"id": lead_id}
            )
            c_row = check.fetchone()
            if c_row and c_row[0] is not None and c_row[0] != user.selected_organization_id:
                raise HTTPException(status_code=403, detail="Cannot edit contact from another workspace")

        updates = []
        params: Dict[str, Any] = {"id": lead_id}

        if request.status is not None:
            updates.append("status = :status")
            params["status"] = request.status

        if request.notes is not None:
            updates.append("notes = :notes")
            params["notes"] = request.notes

        if request.name is not None:
            updates.append("name = :name")
            params["name"] = request.name

        if request.phone is not None:
            updates.append("phone = :phone")
            params["phone"] = request.phone

        if request.email is not None:
            updates.append("email = :email")
            params["email"] = request.email

        if request.company is not None:
            updates.append("company = :company")
            params["company"] = request.company

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
async def delete_lead(
    lead_id: int,
    user: Optional[UserModel] = Depends(get_optional_auth_user),
):
    """Delete a lead enquiry scoped to current workspace."""
    await ensure_table()
    async with db_client.async_session() as session:
        if user and user.selected_organization_id:
            check = await session.execute(
                text("SELECT organization_id FROM leads WHERE id = :id"), {"id": lead_id}
            )
            c_row = check.fetchone()
            if c_row and c_row[0] is not None and c_row[0] != user.selected_organization_id:
                raise HTTPException(status_code=403, detail="Cannot delete contact from another workspace")

        res = await session.execute(text("DELETE FROM leads WHERE id = :id RETURNING id;"), {"id": lead_id})
        await session.commit()
        row = res.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Lead not found")

        return {"ok": True, "lead_id": lead_id, "message": "Lead deleted successfully"}

