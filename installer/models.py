"""Installation facts and UI selection rules, independent of widgets and network I/O."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path


class Presence(StrEnum):
    DETECTED = "detected"
    NOT_FOUND = "not_found"
    PATH_INVALID = "path_invalid"
    UNKNOWN = "unknown"


class IntegrationStatus(StrEnum):
    AVAILABLE = "available"
    INSTALLED_MANAGED = "installed_managed"
    PARTIAL = "partial"
    BROKEN = "broken"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


STATUS_LABELS = {
    IntegrationStatus.AVAILABLE: "可安装",
    IntegrationStatus.INSTALLED_MANAGED: "已安装",
    IntegrationStatus.PARTIAL: "部分安装 · 需要修复",
    IntegrationStatus.BROKEN: "异常 · 查看详情",
    IntegrationStatus.EXTERNAL: "外部接入 · 未纳入管理",
    IntegrationStatus.UNKNOWN: "状态未知",
}


class ManagerError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class TargetState:
    profile_id: str
    display_name: str
    presence: Presence
    client_path: str | None
    status: IntegrationStatus
    skill_present: bool
    mcp_entry_present: bool
    runtime_present: bool
    receipt_valid: bool
    config_path: Path
    skill_path: Path
    detected_version: str | None = None
    problem_codes: tuple[str, ...] = ()

    @property
    def install_eligible(self) -> bool:
        return self.presence == Presence.DETECTED and self.status == IntegrationStatus.AVAILABLE

    @property
    def label(self) -> str:
        if self.status == IntegrationStatus.AVAILABLE and self.presence != Presence.DETECTED:
            return {Presence.NOT_FOUND: "未检测到客户端", Presence.PATH_INVALID: "客户端路径无效"}.get(self.presence, "客户端未知")
        return STATUS_LABELS[self.status]

    def to_dict(self) -> dict:
        value = asdict(self)
        value.update(config_path=str(self.config_path), skill_path=str(self.skill_path), install_eligible=self.install_eligible, label=self.label)
        return value


class SelectionModel:
    """Every selection route, including keyboard/select-all, obeys the same eligibility."""

    def __init__(self):
        self.states: dict[str, TargetState] = {}
        self.selected: set[str] = set()
        self.initialized = False

    def refresh(self, states: dict[str, TargetState]) -> None:
        self.states = states
        self.selected.intersection_update(key for key, state in states.items() if state.install_eligible)
        if not self.initialized:
            if "codex" in states and states["codex"].install_eligible:
                self.selected.add("codex")
            self.initialized = True

    def choose(self, key: str, enabled: bool = True) -> bool:
        state = self.states.get(key)
        if not state or not state.install_eligible:
            self.selected.discard(key)
            return False
        if enabled:
            self.selected.add(key)
        else:
            self.selected.discard(key)
        return True

    def select_all(self) -> None:
        self.selected = {key for key, state in self.states.items() if state.install_eligible}

