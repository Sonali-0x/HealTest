"""Pydantic request/response schemas for the REST API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str
    app_url: str
    test_path: str
    retry_limit: int = Field(default=3, ge=1, le=5)


class ProjectOut(BaseModel):
    id: int
    name: str
    app_url: str
    test_path: str
    retry_limit: int


class RunOut(BaseModel):
    id: int
    project_id: int
    status: str
    total_tests: int = 0
    failed_tests: int = 0


class PatchOut(BaseModel):
    id: int
    test_result_id: int
    status: str
    rationale: str = ""
