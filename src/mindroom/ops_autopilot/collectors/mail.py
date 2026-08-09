"""Mail signal collector (fail-soft).

Implements the ``BaseCollector`` interface. It checks the MindRoom credential
store for Gmail OAuth credentials (service ``google_gmail_oauth``). No Gmail
credentials exist in the runtime environment, so this collector returns a
fail-soft ``CollectResult`` with ``ok=False``, ``error="credentials unavailable"``
and no items. It never fabricates credentials or placeholder data.
"""

from __future__ import annotations

from typing import Any

from mindroom.ops_autopilot.collectors.base import BaseCollector, CollectResult

# Credential service name used by the built-in Gmail OAuth provider.
_CREDENTIAL_SERVICE = "google_gmail_oauth"


class MailCollector(BaseCollector):
    """Report the mail signal, failing soft when Gmail credentials are absent."""

    name = "mail"

    def __init__(self, credentials_manager: Any | None = None) -> None:
        # Dependency-injected credentials manager (for tests); resolved lazily
        # from the runtime when None.
        self._credentials_manager = credentials_manager

    def _load_credentials(self) -> dict[str, Any] | None:
        manager = self._credentials_manager
        if manager is None:
            from mindroom.constants import resolve_runtime_paths
            from mindroom.credentials import get_runtime_credentials_manager

            manager = get_runtime_credentials_manager(resolve_runtime_paths())
        return manager.load_credentials(_CREDENTIAL_SERVICE)

    def collect(self) -> CollectResult:
        try:
            credentials = self._load_credentials()
        except Exception as exc:  # noqa: BLE001 - collect must never raise
            return CollectResult(
                self.name,
                False,
                data={"items": []},
                error=f"credentials unavailable: {type(exc).__name__}",
            )
        if not credentials:
            return CollectResult(
                self.name,
                False,
                data={"items": []},
                error="credentials unavailable",
            )
        # Credentials exist but the real Gmail integration is not implemented;
        # still fail soft with no items rather than fabricate data.
        return CollectResult(
            self.name,
            False,
            data={"items": []},
            error="credentials unavailable",
        )