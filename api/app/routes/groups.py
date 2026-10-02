from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import groups
from ..db import Database, get_db
from ..filters import TrafficFilter, traffic_filter
from .traffic import limit_param

router = APIRouter(tags=["groups"])


class GroupBody(BaseModel):
    kind: Literal["ip", "if"]
    name: str = Field(..., min_length=1, max_length=48)
    members: list[str] = Field(..., min_length=1, max_length=500)
    notes: str = Field("", max_length=500)


@router.get("/groups", summary="IP groups and interface groups")
def list_groups(kind: Literal["ip", "if"] | None = None, db: Database = Depends(get_db)):
    return {"items": groups.list_groups(db, kind)}


@router.put("/groups", summary="Create or replace a group")
def put_group(body: GroupBody, db: Database = Depends(get_db)):
    try:
        return groups.save_group(db, body.kind, body.name, body.members, body.notes)
    except groups.GroupError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/groups", summary="Delete a group")
def delete_group(kind: Literal["ip", "if"], name: str = Query(..., max_length=48), db: Database = Depends(get_db)):
    groups.delete_group(db, kind, name)
    return {"status": "deleted"}


@router.get("/traffic/top-groups", summary="Traffic per IP group (source or destination)")
def top_groups(
    f: TrafficFilter = Depends(traffic_filter),
    direction: Literal["src", "dst"] = Query("src"),
    limit: int = Depends(limit_param),
    db: Database = Depends(get_db),
):
    return groups.top_groups(db, f, direction, limit)


@router.get("/traffic/group-matrix", summary="Traffic between IP groups (source group x destination group)")
def group_matrix(f: TrafficFilter = Depends(traffic_filter), db: Database = Depends(get_db)):
    return groups.group_matrix(db, f)
