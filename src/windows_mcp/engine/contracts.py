"""Shared, serializable contracts. Dispatch is never evidence of completion."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, TypeAdapter, model_validator


class Status(StrEnum):
    VERIFIED = "VERIFIED"
    DISPATCHED_UNVERIFIED = "DISPATCHED_UNVERIFIED"
    TARGET_STALE = "TARGET_STALE"
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
    BLOCKED_BY_POLICY = "BLOCKED_BY_POLICY"
    FOCUS_MISMATCH = "FOCUS_MISMATCH"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class Bounds(BaseModel):
    left: int
    top: int
    right: int
    bottom: int


class SemanticElement(BaseModel):
    element_id: str
    source: Literal["native", "uia", "ia2", "playwright", "visual"] = "uia"
    role: str
    name: str
    automation_id: str = ""
    enabled: bool = True
    visible: bool = True
    bounds: Bounds | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    actions: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    frame_id: str | None = None


class Observation(BaseModel):
    observation_id: str
    session_id: str
    revision: int
    context: dict[str, Any]
    elements: list[SemanticElement] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    truncated: bool = False
    diagnostics: list[str] = Field(default_factory=list)
    aria_snapshot: str | None = None
    audit_id: str | None = None


class Verification(BaseModel):
    passed: bool | None = None
    evidence_type: str = "none"
    detail: str = "No postcondition was supplied or independently observed."


class ActionResult(BaseModel):
    status: Status
    target: str | None = None
    method: str = "none"
    precondition_passed: bool = False
    input_dispatched: bool = False
    verification: Verification = Field(default_factory=Verification)
    new_revision: int | None = None
    retryable: bool = False
    errors: list[str] = Field(default_factory=list)
    audit_id: str | None = None
    fallback_reason: str | None = None
    pending_action_id: str | None = None


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextRangeSpec(StrictInput):
    """Exact literal search OR a provider-defined, one-based text unit range."""

    text: str | None = Field(default=None, min_length=1, max_length=10000)
    occurrence: int | None = Field(default=None, ge=1, le=1000, strict=True)
    ignore_case: StrictBool = False
    unit: Literal["character", "word", "line", "paragraph", "document"] | None = None
    index: int = Field(default=1, ge=1, le=10000, strict=True)
    count: int = Field(default=1, ge=1, le=100, strict=True)

    @model_validator(mode="after")
    def range_kind(self):
        if (self.text is None) == (self.unit is None):
            raise ValueError("Specify exactly one of text or unit")
        if self.text is not None and (self.index != 1 or self.count != 1):
            raise ValueError("index/count apply only to unit ranges")
        if self.unit is not None and (self.occurrence is not None or self.ignore_case):
            raise ValueError("occurrence/ignore_case apply only to text search")
        if self.unit == "document" and (self.index != 1 or self.count != 1):
            raise ValueError("document requires index=1 and count=1")
        return self


class FileExists(StrictInput):
    kind: Literal["file_exists"]
    path: str = Field(min_length=1)


class FileContent(StrictInput):
    kind: Literal["file_content"]
    path: str = Field(min_length=1)
    content: str = Field(max_length=10000000)
    encoding: Literal["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "ascii"] = "utf-8"


class FileSHA256(StrictInput):
    kind: Literal["file_sha256"]
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ControlState(StrictInput):
    value: str | None = None
    enabled: StrictBool | None = None
    visible: StrictBool | None = None
    focused: StrictBool | None = None
    read_only: StrictBool | None = None
    selected: StrictBool | None = None
    toggle: Literal["on", "off", "indeterminate"] | None = None
    expanded: Literal["expanded", "collapsed", "partially-expanded", "leaf"] | None = None
    range: tuple[float, float | None, float | None] | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("state must contain at least one non-null supported property")
        return self


class ElementState(StrictInput):
    kind: Literal["element_state"]
    target: str = Field(min_length=1)
    state: ControlState


class WindowExists(StrictInput):
    kind: Literal["window_exists"]
    window: str = Field(min_length=1)


Expected = Annotated[FileExists | FileContent | FileSHA256 | ElementState | WindowExists, Field(discriminator="kind")]
_expected_adapter = TypeAdapter(Expected)


class BrowserStringExpected(StrictInput):
    kind: Literal["url", "text", "value"]
    value: str = Field(max_length=10000)


class BrowserCheckedExpected(StrictInput):
    kind: Literal["checked"]
    value: StrictBool


BrowserExpected = Annotated[BrowserStringExpected | BrowserCheckedExpected, Field(discriminator="kind")]
_browser_expected_adapter = TypeAdapter(BrowserExpected)


def normalize_expected(value, *, browser: bool = False) -> dict | None:
    if value is None:
        return None
    adapter = _browser_expected_adapter if browser else _expected_adapter
    return adapter.validate_python(value).model_dump(mode="json", exclude_unset=True)


class ObservationResponse(Observation):
    """One object schema preserves the existing top-level wire shape on both MCP SDKs."""

    observation_id: str | None = None
    session_id: str | None = None
    revision: int | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    status: Literal["TIMEOUT", "CANCELLED", "TARGET_STALE", "FAILED"] | None = None

    @model_validator(mode="after")
    def success_identity(self):
        if self.status is None and any(value is None for value in (self.observation_id, self.session_id, self.revision)):
            raise ValueError("A successful observation requires observation_id, session_id and revision")
        return self


class WatchEvent(BaseModel):
    event: Literal["focus", "structure", "property"]
    window_id: int | None = None
    runtime_id: tuple[int, ...] | None = None
    property_id: int | None = None
    revision: int


class WatchResult(BaseModel):
    status: Status
    subscription_id: str | None = None
    revision: int | None = None
    events: list[WatchEvent] = Field(default_factory=list)
    overflow: bool = False
    verification: Verification | None = None
    observation: Observation | None = None
    diagnostics: list[str] = Field(default_factory=list)
    audit_id: str | None = None
