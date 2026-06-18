"""Backward-compatible import path for the profession-independent adapter."""

from .generic_adapter import legacy_tasks_to_plan_request

__all__ = ["legacy_tasks_to_plan_request"]
