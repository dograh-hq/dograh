import json
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import text

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user

router = APIRouter(
    prefix="/contacts",
    tags=["contacts"],
)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS organization_contacts (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    name VARCHAR(255) NOT NULL,
    phone VARCHAR(100) NOT NULL,
    email VARCHAR(255),
    company VARCHAR(255),
    city VARCHAR(100),
    status VARCHAR(50) DEFAULT 'valid',
    called BOOLEAN DEFAULT FALSE,
    last_called_at TIMESTAMPTZ,
    intent VARCHAR(50),
    campaign_id INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
"""
CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_org_contacts_org_id ON organization_contacts(organization_id)
"""

# Unique constraint to prevent duplicate phone numbers per organization
CREATE_UNIQUE_INDEX_SQL = """
DO $$
BEGIN
    -- Delete existing duplicate phone numbers, keeping the latest row
    DELETE FROM organization_contacts a USING organization_contacts b
    WHERE a.id < b.id AND a.organization_id = b.organization_id AND a.phone = b.phone;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_org_contacts_org_phone'
    ) THEN
        ALTER TABLE organization_contacts
        ADD CONSTRAINT uq_org_contacts_org_phone UNIQUE (organization_id, phone);
    END IF;
END;
$$;
"""

DEFAULT_CONTACTS = []


def normalize_phone_number(raw: str) -> str:
    """Normalize phone number to standard E.164 (+91 for 10-digit Indian numbers)."""
    if not raw:
        return ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        local = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        local = digits[1:]
    elif len(digits) == 10:
        local = digits
    else:
        raw_str = str(raw).strip()
        return raw_str if raw_str.startswith("+") else f"+{raw_str}"
    return f"+91{local}"


async def ensure_table():
    try:
        async with db_client.async_session() as session:
            await session.execute(text(CREATE_TABLE_SQL))
            await session.commit()
        async with db_client.async_session() as session:
            await session.execute(text(CREATE_INDEX_SQL))
            await session.commit()
        # Enforce uniqueness of phone per organization
        try:
            async with db_client.async_session() as session:
                await session.execute(text(CREATE_UNIQUE_INDEX_SQL))
                await session.commit()
        except Exception as ce:
            logger.debug(f"uq_org_contacts_org_phone note: {ce}")
    except Exception as e:
        logger.warning(f"ensure_table for organization_contacts error: {e}")


class ContactCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=5)
    email: Optional[str] = None
    company: Optional[str] = None
    city: Optional[str] = None
    status: Optional[str] = "valid"
    campaign_id: Optional[int] = None


class BulkContactsRequest(BaseModel):
    contacts: List[ContactCreateRequest]


class BulkDeleteRequest(BaseModel):
    ids: List[int]


class AssignCampaignRequest(BaseModel):
    ids: List[int]
    campaign_id: int


def _row_to_contact(row: Any) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name or "—",
        "phone": row.phone,
        "email": row.email or "",
        "company": row.company or "—",
        "city": row.city or "",
        "status": row.status or "valid",
        "called": bool(row.called),
        "lastCalledAt": row.last_called_at.isoformat() if row.last_called_at else None,
        "intent": row.intent,
        "campaignId": str(row.campaign_id) if row.campaign_id else None,
    }


@router.get("")
@router.get("/")
async def list_organization_contacts(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    q: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    campaign_id: Optional[int] = Query(None),
    user: UserModel = Depends(get_user),
):
    """
    Get phonebook contacts for the user's organization with server-side pagination & filtering.
    """
    await ensure_table()
    org_id = user.selected_organization_id or 1

    where_clauses = ["organization_id = :org_id"]
    params: Dict[str, Any] = {"org_id": org_id, "limit": limit, "offset": offset}

    if campaign_id is not None:
        where_clauses.append("campaign_id = :campaign_id")
        params["campaign_id"] = campaign_id

    if status and status != "all":
        if status == "called":
            where_clauses.append("called = TRUE")
        elif status == "not-called":
            where_clauses.append("called = FALSE")
        else:
            where_clauses.append("status = :status")
            params["status"] = status

    if q and q.strip():
        where_clauses.append(
            "(name ILIKE :q OR phone ILIKE :q OR email ILIKE :q OR company ILIKE :q OR city ILIKE :q)"
        )
        params["q"] = f"%{q.strip()}%"

    where_sql = " AND ".join(where_clauses)

    async with db_client.async_session() as session:
        query = text(
            f"""
            SELECT id, name, phone, email, company, city, status, called, last_called_at, intent, campaign_id
            FROM organization_contacts
            WHERE {where_sql}
            ORDER BY id ASC
            LIMIT :limit OFFSET :offset
            """
        )
        result = await session.execute(query, params)
        rows = result.fetchall()

        count_query = text(
            f"""
            SELECT COUNT(*) FROM organization_contacts
            WHERE {where_sql}
            """
        )
        count_result = await session.execute(count_query, params)
        total_count = count_result.scalar() or 0

        page = (offset // limit) + 1 if limit > 0 else 1
        total_pages = max(1, (total_count + limit - 1) // limit) if limit > 0 else 1
        return {
            "contacts": [_row_to_contact(r) for r in rows],
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
        }


@router.post("")
@router.post("/")
async def create_organization_contact(
    req: ContactCreateRequest,
    user: UserModel = Depends(get_user),
):
    """Add a new contact to the organization's calling list, deduplicating by phone."""
    await ensure_table()
    org_id = user.selected_organization_id or 1
    norm_phone = normalize_phone_number(req.phone)
    if not norm_phone:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    async with db_client.async_session() as session:
        res = await session.execute(
            text(
                """
                INSERT INTO organization_contacts 
                (organization_id, name, phone, email, company, city, status, called, campaign_id, created_at, updated_at)
                VALUES (:org_id, :name, :phone, :email, :company, :city, :status, FALSE, :campaign_id, NOW(), NOW())
                ON CONFLICT (organization_id, phone) DO UPDATE
                    SET name = EXCLUDED.name,
                        email = COALESCE(EXCLUDED.email, organization_contacts.email),
                        company = COALESCE(EXCLUDED.company, organization_contacts.company),
                        city = COALESCE(EXCLUDED.city, organization_contacts.city),
                        updated_at = NOW()
                RETURNING id, name, phone, email, company, city, status, called, last_called_at, intent, campaign_id
                """
            ),
            {
                "org_id": org_id,
                "name": req.name,
                "phone": norm_phone,
                "email": req.email,
                "company": req.company,
                "city": req.city,
                "status": req.status or "valid",
                "campaign_id": req.campaign_id,
            },
        )
        row = res.fetchone()
        await session.commit()
        return _row_to_contact(row)


@router.post("/bulk")
async def bulk_import_organization_contacts(
    req: BulkContactsRequest,
    user: UserModel = Depends(get_user),
):
    """Bulk import contacts from CSV or external list into organization phonebook without duplicates."""
    await ensure_table()
    org_id = user.selected_organization_id or 1

    inserted: List[Dict[str, Any]] = []
    seen_in_batch = set()
    async with db_client.async_session() as session:
        for c in req.contacts:
            norm_phone = normalize_phone_number(c.phone)
            if not norm_phone or norm_phone in seen_in_batch:
                continue
            seen_in_batch.add(norm_phone)
            res = await session.execute(
                text(
                    """
                    INSERT INTO organization_contacts 
                    (organization_id, name, phone, email, company, city, status, called, campaign_id, created_at, updated_at)
                    VALUES (:org_id, :name, :phone, :email, :company, :city, :status, FALSE, :campaign_id, NOW(), NOW())
                    ON CONFLICT (organization_id, phone) DO UPDATE
                        SET name = EXCLUDED.name,
                            email = COALESCE(EXCLUDED.email, organization_contacts.email),
                            company = COALESCE(EXCLUDED.company, organization_contacts.company),
                            city = COALESCE(EXCLUDED.city, organization_contacts.city),
                            updated_at = NOW()
                    RETURNING id, name, phone, email, company, city, status, called, last_called_at, intent, campaign_id
                    """
                ),
                {
                    "org_id": org_id,
                    "name": c.name,
                    "phone": norm_phone,
                    "email": c.email,
                    "company": c.company,
                    "city": c.city,
                    "status": c.status or "valid",
                    "campaign_id": c.campaign_id,
                },
            )
            row = res.fetchone()
            if row:
                inserted.append(_row_to_contact(row))
        await session.commit()

    return {"count": len(inserted), "contacts": inserted}


@router.post("/delete-bulk")
async def delete_contacts_bulk(
    req: BulkDeleteRequest,
    user: UserModel = Depends(get_user),
):
    """Delete selected contacts belonging to the user's organization."""
    if not req.ids:
        return {"deleted": 0}
    org_id = user.selected_organization_id or 1

    async with db_client.async_session() as session:
        await session.execute(
            text(
                """
                DELETE FROM organization_contacts
                WHERE organization_id = :org_id AND id = ANY(:ids)
                """
            ),
            {"org_id": org_id, "ids": req.ids},
        )
        await session.commit()

    return {"deleted": len(req.ids)}


@router.post("/assign-campaign")
async def assign_contacts_to_campaign(
    req: AssignCampaignRequest,
    user: UserModel = Depends(get_user),
):
    """Assign selected contacts to a specific campaign."""
    if not req.ids:
        return {"updated": 0}
    org_id = user.selected_organization_id or 1

    async with db_client.async_session() as session:
        await session.execute(
            text(
                """
                UPDATE organization_contacts
                SET campaign_id = :campaign_id, updated_at = NOW()
                WHERE organization_id = :org_id AND id = ANY(:ids)
                """
            ),
            {"org_id": org_id, "ids": req.ids, "campaign_id": req.campaign_id},
        )
        await session.commit()

    return {"updated": len(req.ids), "campaign_id": req.campaign_id}
