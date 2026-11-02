"""Pydantic request/response schemas for the REST API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    app_url: str = Field(min_length=8, max_length=500)
    test_path: str = Field(min_length=1, max_length=500)
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
    flaky_tests: int = 0


class PatchOut(BaseModel):
    id: int
    test_result_id: int
    status: str
    rationale: str = ""
    approved_copy_path: str | None = None


class BenchmarkCreate(BaseModel):
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    apps: list[str] = Field(default_factory=lambda: ["todo", "shop", "multistep-form"])
    mutation_types: list[str] = Field(
        default_factory=lambda: [
            "renamed_id",
            "button_text",
            "moved_element",
            "new_wrapper",
            "cookie_modal",
            "extra_flow_step",
            "button_to_link",
        ]
    )
    cost_per_1k_tokens: float | None = Field(default=None, ge=0)
