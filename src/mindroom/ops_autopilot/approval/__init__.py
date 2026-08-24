"""ARIP-style approval gate for the ops autopilot."""

from __future__ import annotations

from mindroom.ops_autopilot.approval.gate import ApprovalGate, ApprovalOutcome, request_approval

__all__ = ["ApprovalGate", "ApprovalOutcome", "request_approval"]