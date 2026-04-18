"""Runbook YAML schema — pydantic models with path-aware validation errors."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

# Watch kinds the engine supports. Adding a new kind means:
#   1. add it here
#   2. implement a watcher in watcher.py
WatchKind = Literal[
    "pod",
    "node",
    "node-condition",
    "crd",
    "mongo-event",
    "pod-event",
    "taint",
    "log",
    "training-log",
]


def _parse_duration(d: str) -> int:
    """'60s' → 60, '2m' → 120, '1h' → 3600."""
    if not d:
        return 0
    unit = d[-1]
    try:
        value = int(d[:-1])
    except ValueError as e:
        raise ValueError(f"invalid duration: {d!r}") from e
    if unit == "s":
        return value
    if unit == "m":
        return value * 60
    if unit == "h":
        return value * 3600
    raise ValueError(f"duration must end with s/m/h, got: {d!r}")


class Prerequisite(BaseModel):
    name: str
    check: str
    expect: str

    model_config = {"extra": "forbid"}


class Action(BaseModel):
    script: str
    args: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class Watch(BaseModel):
    kind: WatchKind

    # Per-kind optional fields (all Optional, validated contextually by watchers)
    selector: Optional[str] = None
    namespace: Optional[str] = None
    type: Optional[str] = None
    status: Optional[str] = None
    expect: Optional[str] = None
    pattern: Optional[str] = None
    collection: Optional[str] = None
    filter: Optional[str] = None
    group: Optional[str] = None
    resource: Optional[str] = None
    pod: Optional[str] = None
    field: Optional[str] = None
    reason: Optional[str] = None
    key: Optional[str] = None

    model_config = {"extra": "forbid"}


class Stage(BaseModel):
    id: str
    title: str
    action: Optional[Action] = None
    watch: list[Watch] = Field(default_factory=list)
    hook: Optional[str] = None
    expect: list[str] = Field(default_factory=list)
    timeout: str = "60s"
    dwell: str = "0s"

    model_config = {"extra": "forbid"}

    @field_validator("timeout", "dwell")
    @classmethod
    def _validate_duration(cls, v: str) -> str:
        _parse_duration(v)
        return v

    @property
    def timeout_seconds(self) -> int:
        return _parse_duration(self.timeout)

    @property
    def dwell_seconds(self) -> int:
        return _parse_duration(self.dwell)


class RunbookMetadata(BaseModel):
    name: str
    nickname: Optional[str] = None   # e.g. "rogue-moose" — the over-Zoom callable name
    title: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    estimatedDuration: str = "60s"

    model_config = {"extra": "forbid"}


class Runbook(BaseModel):
    apiVersion: str = "nvsx/v1"
    kind: str = "Runbook"
    metadata: RunbookMetadata
    prerequisites: list[Prerequisite] = Field(default_factory=list)
    stages: list[Stage]
    narration: dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @field_validator("apiVersion")
    @classmethod
    def _check_api_version(cls, v: str) -> str:
        if not v.startswith("nvsx/"):
            raise ValueError(f"apiVersion must start with 'nvsx/', got: {v!r}")
        return v

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        if v != "Runbook":
            raise ValueError(f"kind must be 'Runbook', got: {v!r}")
        return v

    @classmethod
    def from_path(cls, path: Path) -> "Runbook":
        raw = yaml.safe_load(path.read_text())
        return cls.model_validate(raw)

    def stage_by_id(self, stage_id: str) -> Optional[Stage]:
        return next((s for s in self.stages if s.id == stage_id), None)
