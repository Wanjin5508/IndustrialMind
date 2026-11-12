"""Durable method-scoped fault plans and explicit process-death failpoints."""

from collections.abc import Callable


class SimulatedCrash(BaseException):
    """Bypass ordinary transport handling as a process death would."""


class Failpoints:
    def __init__(self, callback: Callable[[str], None] | None = None):
        self.callback = callback

    def hit(self, name: str) -> None:
        if self.callback:
            self.callback(name)


class FaultPlan:
    def __init__(self, store):
        self.store = store

    def add(self, method: str, kind: str, times: int = 1) -> None:
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO faults(method,kind,remaining) VALUES(?,?,?)", (method, kind, times)
            )

    def take(self, method: str) -> str | None:
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id,kind FROM faults WHERE method=? AND remaining>0 ORDER BY id LIMIT 1",
                (method,),
            ).fetchone()
            if row:
                conn.execute("UPDATE faults SET remaining=remaining-1 WHERE id=?", (row["id"],))
                return row["kind"]
        return None
