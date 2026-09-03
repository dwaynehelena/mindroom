"""Personal Ops Autopilot (P8).

A self-contained operational pipeline that collects live system/git/schedule
signals, composes a bounded ops brief, passes it through an explicit ARIP
approval gate, delivers it to the operator's Telegram DM via the local Matrix
portal room bridge, and wires a recurring 07:30 local cron.

Pipeline flow:

    collect -> compose -> gate -> deliver

Modules
-------
- ``collectors`` : signal sources (git via gh CLI, scheduler, deferred mail/calendar, base contract)
- ``composer``   : turns collected signals into a bounded human-readable brief
- ``approval``   : ARIP-style approval gate reusing approval_manager
- ``delivery``   : Telegram delivery through the Matrix portal bridge
- ``orchestrator`` : end-to-end runner
- ``scheduler_hook`` : recurring 07:30 local scheduling via scheduling.schedule_task

This package reuses the exact Matrix delivery pattern from
``scripts/heartbeat_broadcast.py``.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]