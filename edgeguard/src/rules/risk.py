from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RiskWeights:
    pick: float = 1.0
    rapid_multi_pick: float = 2.0
    conceal: float = 15.0
    lost_uncertain: float = 3.0
    checkout_mismatch: float = 20.0
    exit_with_unpaid: float = 30.0
    put_back: float = -5.0
    full_reconciliation: float = -10.0


@dataclass
class RiskState:
    score: float = 0.0
    last_update_ts: datetime | None = None
    last_decay_ts: datetime | None = None
    last_suspicious_ts: datetime | None = None
    history: list[tuple[datetime, float, str]] = field(default_factory=list)


class RiskEngine:
    def __init__(self, weights: RiskWeights | None = None, decay_per_30s: float = 2.0) -> None:
        self.weights = weights or RiskWeights()
        self.decay_per_30s = decay_per_30s
        self.state: dict[str, RiskState] = {}

    def _get(self, customer_id: str) -> RiskState:
        if customer_id not in self.state:
            self.state[customer_id] = RiskState()
        return self.state[customer_id]

    @staticmethod
    def clamp(score: float) -> float:
        return max(0.0, min(100.0, score))

    def decay(
        self,
        customer_id: str,
        now: datetime,
        allow_decay: bool = True,
        suspicious_activity: bool = False,
    ) -> float:
        s = self._get(customer_id)
        if suspicious_activity:
            s.last_suspicious_ts = now
        if s.last_update_ts is None:
            s.last_update_ts = now
            s.last_decay_ts = now
            return s.score
        if not allow_decay:
            s.last_update_ts = now
            return s.score

        base_ts = s.last_decay_ts or s.last_update_ts
        if s.last_suspicious_ts is not None and s.last_suspicious_ts > base_ts:
            base_ts = s.last_suspicious_ts
        elapsed = max(0.0, (now - base_ts).total_seconds())
        steps = int(elapsed // 30.0)
        if steps > 0:
            s.score = self.clamp(s.score - steps * self.decay_per_30s)
            s.last_decay_ts = now
            s.last_update_ts = now
        return s.score

    def apply_delta(
        self,
        customer_id: str,
        delta: float,
        reason: str,
        now: datetime,
        allow_decay: bool = True,
    ) -> float:
        s = self._get(customer_id)
        self.decay(customer_id, now, allow_decay=allow_decay, suspicious_activity=delta > 0.0)
        s.score = self.clamp(s.score + delta)
        s.last_update_ts = now
        s.history.append((now, delta, reason))
        return s.score

    @staticmethod
    def band(score: float) -> str:
        if score >= 50.0:
            return "RED"
        if score >= 25.0:
            return "YELLOW"
        return "GREEN"
