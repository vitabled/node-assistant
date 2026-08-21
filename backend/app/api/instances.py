"""Workspace instance API."""
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services import accounts, instances

router = APIRouter(prefix="/api/instances", tags=["instances"])


class Instance(BaseModel):
    id: str
    name: str
    account_id: str


class InstanceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


def _account_id() -> str:
    account_id = accounts.current_account.get()
    if not account_id:
        raise HTTPException(401, "Требуется авторизация")
    return account_id


@router.get("", response_model=list[Instance])
async def list_all():
    return instances.list_instances(_account_id())


@router.post("", response_model=Instance, status_code=status.HTTP_201_CREATED)
async def create(body: InstanceCreate):
    try:
        return instances.create(body.name, _account_id())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc