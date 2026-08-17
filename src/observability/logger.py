"""Writes everything under /evidence/. Every log_event() call runs its
fields through redact_dict first - the single choke point for redaction,
so nothing bypasses it regardless of caller. See REPORT.md, "Safety".
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from playwright.sync_api import Page

from src.safety.config import SafetyConfig
from src.safety.redact import redact_dict, redact_text_recursive


class EvidenceLogger:
    def __init__(
        self,
        run_id: str,
        run_type: Literal["discovery", "replay"],
        safety: SafetyConfig,
        evidence_root: Path = Path("evidence"),
    ):
        self.run_id = run_id
        self.run_type = run_type
        self.safety = safety
        self.dir = evidence_root / run_type / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "screenshots").mkdir(exist_ok=True)
        self._log_path = self.dir / "log.jsonl"
        # Known real secret values for this run (e.g. the actual password) -
        # scrubbed from anything written, in addition to the field-name-based
        # redact_dict pass. Populated via add_secret() by whoever knows the
        # real values (BrowserTools' secrets dict, replay's params). See
        # project memory: a real password leaked into evidence before this
        # existed, via an accessibility-tree observation string, which
        # field-name-based redaction alone can't catch.
        self._secret_values: list[str] = []

    def add_secret(self, value: str) -> None:
        if value and value not in self._secret_values:
            self._secret_values.append(value)

    def _redact(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            obj = redact_dict(obj, self.safety)
        return redact_text_recursive(obj, self._secret_values)

    def log_event(self, event: str, **fields: Any) -> None:
        safe_fields = self._redact(fields)
        record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **safe_fields}
        with self._log_path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def save_screenshot(self, page: Page, label: str) -> Path:
        path = self.dir / "screenshots" / f"{label}.png"
        page.screenshot(path=str(path))
        return path

    def save_raw_run(self, raw_log_json: dict) -> Path:
        safe = self._redact(raw_log_json)
        path = self.dir / "raw_run.json"
        path.write_text(json.dumps(safe, indent=2, default=str))
        return path

    def finalize(self, summary: dict) -> Path:
        safe_summary = self._redact(summary)
        path = self.dir / "summary.json"
        path.write_text(json.dumps(safe_summary, indent=2, default=str))
        return path
