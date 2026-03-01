from datetime import datetime
import uuid


class SessionManager:
    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}

    def open_or_get_session(self, customer_id: str, ts: datetime) -> str:
        if customer_id in self.sessions:
            return self.sessions[customer_id]["session_id"]
        session_id = f"SES-{uuid.uuid4()}"
        self.sessions[customer_id] = {
            "session_id": session_id,
            "customer_id": customer_id,
            "opened_at": ts,
            "last_seen": ts,
            "state": "ACTIVE",
            "close_reason": None,
        }
        return session_id

    def update_customer_presence(self, customer_id: str, camera_id: str, ts: datetime) -> None:
        session = self.sessions.get(customer_id)
        if session is None:
            self.open_or_get_session(customer_id, ts)
            session = self.sessions[customer_id]
        session["last_seen"] = ts
        session["camera_id"] = camera_id

    def close_session(self, customer_id: str, reason: str, ts: datetime) -> dict:
        session = self.sessions.get(customer_id)
        if session is None:
            session_id = self.open_or_get_session(customer_id, ts)
            session = self.sessions[customer_id]
            session["session_id"] = session_id
        session["closed_at"] = ts
        session["state"] = "CLOSED"
        session["close_reason"] = reason
        return dict(session)
