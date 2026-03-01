from dataclasses import dataclass

from src.rules.theft_state_machine import BasketState


@dataclass
class ReconcileResult:
    missing_count: int
    missing_item_ids: list[str]
    resolved: bool


def reconcile_counter(ledger: BasketState, presented_unknown_count: int = 0) -> ReconcileResult:
    possessed = ledger.inferred_total_possessed()
    presented = set(ledger.items_on_counter) if hasattr(ledger, "items_on_counter") else set()
    missing = sorted(list(possessed - presented))
    if presented_unknown_count > 0 and missing:
        missing = missing[presented_unknown_count:]
    return ReconcileResult(
        missing_count=max(0, len(missing)),
        missing_item_ids=missing,
        resolved=len(missing) == 0,
    )


def reconcile_pos(
    ledger: BasketState,
    paid_count: int | None = None,
    paid_item_ids: list[str] | None = None,
) -> ReconcileResult:
    possessed = ledger.inferred_total_possessed()
    if paid_item_ids:
        paid = set(paid_item_ids)
    else:
        paid = set(list(possessed)[: max(0, int(paid_count or 0))])
    missing = sorted(list(possessed - paid))
    return ReconcileResult(
        missing_count=len(missing),
        missing_item_ids=missing,
        resolved=len(missing) == 0,
    )
