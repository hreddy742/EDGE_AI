from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class BasketState:
    items_in_hand: set[str] = field(default_factory=set)
    items_concealed: set[str] = field(default_factory=set)
    items_on_counter: set[str] = field(default_factory=set)
    items_returned: set[str] = field(default_factory=set)

    def inferred_total_possessed(self) -> set[str]:
        return set(self.items_in_hand | self.items_concealed)


@dataclass
class StateEvent:
    event_type: str
    customer_id: str
    camera_id: str
    involved_item_ids: list[str]
    ts_start: datetime
    ts_end: datetime
    explanation: str
    risk_delta: float


class TheftStateMachine:
    """
    Per-customer temporal reasoning core.
    Concrete transition logic is specified in docs/logic_spec.md.
    """

    def __init__(self) -> None:
        self.baskets: dict[str, BasketState] = {}
        self.mismatch_unresolved: dict[str, bool] = {}
        self.lost_uncertain_items: dict[str, set[str]] = {}

    def get_basket(self, customer_id: str) -> BasketState:
        if customer_id not in self.baskets:
            self.baskets[customer_id] = BasketState()
        return self.baskets[customer_id]

    def set_mismatch_unresolved(self, customer_id: str, value: bool) -> None:
        self.mismatch_unresolved[customer_id] = value

    def get_mismatch_unresolved(self, customer_id: str) -> bool:
        return self.mismatch_unresolved.get(customer_id, False)

    def get_lost_uncertain_items(self, customer_id: str) -> set[str]:
        return self.lost_uncertain_items.setdefault(customer_id, set())

    def apply(self, customer_id: str, signal_type: str, item_id: str | None, ts: datetime) -> list[StateEvent]:
        basket = self.get_basket(customer_id)
        events: list[StateEvent] = []
        signal = signal_type.upper().strip()

        def emit(event_type: str, ids: list[str], explanation: str, risk_delta: float) -> None:
            events.append(
                StateEvent(
                    event_type=event_type,
                    customer_id=customer_id,
                    camera_id="",
                    involved_item_ids=ids,
                    ts_start=ts,
                    ts_end=ts,
                    explanation=explanation,
                    risk_delta=risk_delta,
                )
            )

        if signal == "PICK" and item_id:
            basket.items_in_hand.add(item_id)
            basket.items_returned.discard(item_id)
            emit("PICK", [item_id], "Item associated to customer hand.", 1.0)
            return events

        if signal == "PUT_BACK" and item_id:
            basket.items_in_hand.discard(item_id)
            basket.items_concealed.discard(item_id)
            basket.items_on_counter.discard(item_id)
            self.get_lost_uncertain_items(customer_id).discard(item_id)
            basket.items_returned.add(item_id)
            emit("PUT_BACK", [item_id], "Item returned to shelf and removed from possessed set.", -5.0)
            return events

        if signal in {"CONCEAL_POCKET", "CONCEAL_BAG", "CONCEAL_HOODIE", "CONCEAL_PANTS", "CONCEAL_SHIRT"} and item_id:
            basket.items_in_hand.discard(item_id)
            basket.items_concealed.add(item_id)
            self.get_lost_uncertain_items(customer_id).discard(item_id)
            emit(signal, [item_id], "Item transitioned from visible possession to concealment.", 15.0)
            return events

        if signal == "ON_COUNTER" and item_id:
            basket.items_in_hand.discard(item_id)
            basket.items_on_counter.add(item_id)
            self.get_lost_uncertain_items(customer_id).discard(item_id)
            emit("ON_COUNTER", [item_id], "Item presented on counter.", -0.5)
            return events

        if signal == "RAPID_MULTI_PICK":
            emit("RAPID_MULTI_PICK", [], "Rapid multi-pick behavior detected.", 2.0)
            return events

        if signal == "LOST_UNCERTAIN" and item_id:
            self.get_lost_uncertain_items(customer_id).add(item_id)
            emit("LOST_UNCERTAIN", [item_id], "Item disappeared without strong conceal signal.", 3.0)
            return events

        if signal == "COUNTER_MISMATCH":
            self.set_mismatch_unresolved(customer_id, True)
            emit("COUNTER_MISMATCH", [], "Counter mismatch unresolved.", 20.0)
            return events

        if signal == "COUNTER_RECONCILED":
            self.set_mismatch_unresolved(customer_id, False)
            emit("COUNTER_RECONCILED", [], "Counter fully reconciled.", -10.0)
            return events

        if signal == "EXIT":
            concealed_count = len(basket.items_concealed)
            mismatch = self.get_mismatch_unresolved(customer_id)
            if concealed_count > 0 or mismatch:
                missing = sorted(list(basket.items_concealed))
                emit("EXIT_ALERT", missing, "Exit with concealed items or unresolved mismatch.", 30.0)
            else:
                emit("EXIT_CLEARED", [], "Exit crossing with clean reconciliation.", 0.0)
            return events

        return events
