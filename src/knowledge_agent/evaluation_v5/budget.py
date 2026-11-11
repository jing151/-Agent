"""Durable, fail-closed HTTP-attempt budget, shared by live and offline modes."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from ..llm.base import ModelError

ROOT = Path(__file__).resolve().parents[3]
LIVE_LEDGER = ROOT / "examples/evaluation_v5/api-budget.json"
LEGACY_LEDGER = ROOT / "examples/evaluation_v2/reports/api-budget.json"


class BudgetError(ModelError):
    """Safe local control error; never an external model-service failure."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BudgetLedger:
    """Each failed HTTP attempt also consumes a slot. No slot is refunded.

    Atomic replacement plus an exclusive lock protects the counter across
    runner restarts/processes. A stale lock fails closed; it is never broken.
    Offline simulations can never write either official historical ledger.
    """
    def __init__(self, path: Path, *, simulation: bool):
        self.path = Path(path).resolve()
        self.simulation = simulation
        if self.path == LEGACY_LEDGER.resolve() or simulation and self.path == LIVE_LEDGER.resolve():
            raise BudgetError("protected_budget_path")
        self._load()

    @classmethod
    def create_simulation(cls, directory: Path) -> BudgetLedger:
        path = Path(directory).resolve() / "api-budget.json"
        if path in {LIVE_LEDGER.resolve(), LEGACY_LEDGER.resolve()}:
            raise BudgetError("protected_budget_path")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"schema_version": 1, "phase": "evaluation_v5", "ledger_mode": "offline_simulated",
                "max_calls": 40, "attempts": 0, "events": [], "budget_exhausted": False,
                "execution_enabled": False, "authorization_status": "not_applicable_offline_simulation",
                "actual_api_calls": 0}
        with path.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        return cls(path, simulation=True)

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            count = data["attempts"]
            if (data["phase"] != "evaluation_v5" or data["max_calls"] != 40
                    or type(count) is not int or not 0 <= count <= 40
                    or len(data["events"]) != count
                    or [event["number"] for event in data["events"]] != list(range(1, count + 1))
                    or self.simulation != (data.get("ledger_mode") == "offline_simulated")):
                raise ValueError
            return data
        except (OSError, KeyError, ValueError, TypeError):
            raise BudgetError("invalid_budget_ledger") from None

    @property
    def data(self) -> dict[str, Any]:
        return self._load()

    def guard(self) -> None:
        data = self._load()
        if not self.simulation and (self.path != LIVE_LEDGER.resolve()
                or data.get("authorization_status") != "authorized" or data.get("execution_enabled") is not True):
            raise BudgetError("explicit_real_authorization_required")
        if data["attempts"] >= 40:
            raise BudgetError("phase_api_budget_exhausted")

    @contextmanager
    def _lock(self) -> Iterator[None]:
        path = self.path.with_suffix(".lock")
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise BudgetError("budget_busy_or_stale_lock") from None
        try:
            os.close(descriptor)
            yield
        finally:
            path.unlink()  # Only the lock created by this call.

    def _save(self, data: dict[str, Any]) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def reserve(self, execution_id: str, case_id: str) -> int:
        with self._lock():
            self.guard()
            data = self._load()
            number = data["attempts"] + 1
            data["attempts"] = number
            data["budget_exhausted"] = number == 40
            data["events"].append({"number": number, "execution_id": execution_id, "case_id": case_id,
                                   "started_at": now(), "state": "started",
                                   "request_kind": "offline_mock_transport" if self.simulation else "real"})
            self._save(data)  # Durable BEFORE delegate.handle_request().
            return number

    def finish(self, number: int, details: dict[str, Any]) -> None:
        allowed = {"outcome", "elapsed_seconds", "http_status", "response_id", "provider_model", "usage", "error_code"}
        if set(details) - allowed:
            raise BudgetError("unexpected_budget_metadata")
        with self._lock():
            data = self._load()
            if not 1 <= number <= data["attempts"] or data["events"][number - 1]["state"] != "started":
                raise BudgetError("budget_event_already_finished_or_unknown")
            data["events"][number - 1].update(details, state="finished", finished_at=now())
            self._save(data)
