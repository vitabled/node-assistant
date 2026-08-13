"""API Fail2Ban list (Wave-5 PR-2): per-account список IP/CIDR для бана."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import f2b_list

router = APIRouter(prefix="/api/f2b-list")


@router.get("")
async def get_list():
    return {"entries": f2b_list.load()}


class PutBody(BaseModel):
    entries: list[str] = []


@router.put("")
async def put_list(body: PutBody):
    try:
        saved = f2b_list.save(body.entries)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True, "entries": saved, "count": len(saved)}
