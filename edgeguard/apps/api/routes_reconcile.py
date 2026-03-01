from pydantic import BaseModel, Field
from fastapi import APIRouter

from src.core.config import get_settings
from src.pipeline.manager import get_pipeline_manager

router = APIRouter(prefix="/retail", tags=["reconcile"])


class CounterReconcileRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)
    camera_id: str | None = None
    presented_item_ids: list[str] = Field(default_factory=list)
    presented_unknown_count: int = Field(default=0, ge=0)


class CounterReconcileResponse(BaseModel):
    customer_id: str
    missing_count: int
    missing_item_ids: list[str] = Field(default_factory=list)
    resolved: bool


class POSReconcileRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)
    camera_id: str | None = None
    paid_count: int | None = Field(default=None, ge=0)
    paid_item_ids: list[str] = Field(default_factory=list)


class POSReconcileResponse(BaseModel):
    customer_id: str
    paid_count: int | None = None
    paid_item_ids: list[str] = Field(default_factory=list)
    unpaid_item_ids: list[str] = Field(default_factory=list)
    risk_score: float
    emitted_event_ids: list[str] = Field(default_factory=list)


@router.post("/counter/reconcile", response_model=CounterReconcileResponse)
def reconcile_counter(payload: CounterReconcileRequest) -> CounterReconcileResponse:
    settings = get_settings()
    runner = get_pipeline_manager(settings).get_runner(payload.camera_id)
    result = runner.reconcile_counter(
        customer_id=payload.customer_id,
        presented_item_ids=payload.presented_item_ids,
        presented_unknown_count=payload.presented_unknown_count,
    )
    missing = result.get("missing_item_ids", [])
    return CounterReconcileResponse(
        customer_id=payload.customer_id,
        missing_count=int(result.get("missing_count", len(missing))),
        missing_item_ids=missing,
        resolved=bool(result.get("resolved", len(missing) == 0)),
    )


@router.post("/pos/reconcile", response_model=POSReconcileResponse)
def reconcile_pos(payload: POSReconcileRequest) -> POSReconcileResponse:
    settings = get_settings()
    manager = get_pipeline_manager(settings)
    runner = manager.get_runner(payload.camera_id)
    result = runner.reconcile_pos(
        customer_id=payload.customer_id,
        paid_count=payload.paid_count,
        paid_item_ids=payload.paid_item_ids,
    )
    return POSReconcileResponse(**result)
