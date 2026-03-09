from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AnalyzerSnapshot:
    disconnected_count: int
    reconnect_count: int
    streams_zero_count: int
    failure_reason: str


class DaggerLogAnalyzer:
    def __init__(self) -> None:
        self.disconnected_count = 0
        self.reconnect_count = 0
        self.streams_zero_count = 0
        self.failure_reason = ""

    def ingest(self, line: str) -> None:
        lower = line.lower()

        if "oom-kill" in lower or "failed with result 'oom-kill'" in lower:
            self.failure_reason = self.failure_reason or "oom_kill_detected"

        if "] disconnected " in lower:
            self.disconnected_count += 1

        if "reconnect in" in lower:
            self.reconnect_count += 1

        if "streams=0" in lower:
            self.streams_zero_count += 1

        if not self.failure_reason and self._is_reconnect_failure_pattern():
            self.failure_reason = "unstable_reconnect_pattern"

    def is_failure(self) -> bool:
        return bool(self.failure_reason)

    def snapshot(self) -> AnalyzerSnapshot:
        return AnalyzerSnapshot(
            disconnected_count=self.disconnected_count,
            reconnect_count=self.reconnect_count,
            streams_zero_count=self.streams_zero_count,
            failure_reason=self.failure_reason,
        )

    def _is_reconnect_failure_pattern(self) -> bool:
        if self.disconnected_count >= 4 and self.reconnect_count >= 4 and self.streams_zero_count >= 2:
            return True
        return False
