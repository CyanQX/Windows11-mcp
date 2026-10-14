"""Shared, serializable contracts. Dispatch is never evidence of completion."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


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


class Observation(BaseModel):
    observation_id: str
    session_id: str
    revision: int
    context: dict[str, Any]
    elements: list[SemanticElement] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    truncated: bool = False
    diagnostics: list[str] = Field(default_factory=list)


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
