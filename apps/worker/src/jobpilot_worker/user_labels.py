"""Label changes the *user* makes from the dashboard (ADR-0005).

Lives in the worker, outside the MCP package: no agent can reach it. It may add **and remove**
labels, but only labels in the `JobPilot/` namespace, and only for actions triggered by an
authenticated user (re-classification, clearing the suspicious flag).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from jobpilot_core.enums import SUSPICIOUS_LABEL, EmailCategory
from jobpilot_mcp_gmail.gmail_client import LABEL_PREFIX, GmailClient, LabelNotAllowedError

ALL_JOBPILOT_LABELS = [c.gmail_label for c in EmailCategory] + [SUSPICIOUS_LABEL]


class UserLabelActions:
    def __init__(self, gmail: GmailClient) -> None:
        self._gmail = gmail
        self._ids: dict[str, str] | None = None

    async def _label_ids(self) -> dict[str, str]:
        if self._ids is None:
            self._ids = await self._gmail.ensure_labels(ALL_JOBPILOT_LABELS)
        return self._ids

    async def set_labels(
        self, message_id: str, *, add: Sequence[str], remove: Sequence[str]
    ) -> None:
        ids = await self._label_ids()
        _check(add, ids)
        _check(remove, ids)
        payload = {
            "addLabelIds": [ids[n] for n in add],
            "removeLabelIds": [ids[n] for n in remove if n not in add],
        }
        # protected access on purpose: this is the only module allowed to remove labels
        await self._gmail._request("POST", f"/messages/{message_id}/modify", json=payload)

    async def reclassify(
        self, message_id: str, old: EmailCategory | None, new: EmailCategory
    ) -> None:
        remove = [old.gmail_label] if old is not None and old != new else []
        await self.set_labels(message_id, add=[new.gmail_label], remove=remove)

    async def clear_suspicious(self, message_id: str) -> None:
        await self.set_labels(message_id, add=[], remove=[SUSPICIOUS_LABEL])


def _check(names: Sequence[str], ids: Mapping[str, str]) -> None:
    for name in names:
        if not name.startswith(LABEL_PREFIX) or name not in ids:
            raise LabelNotAllowedError(f"user actions only manage JobPilot labels: {name!r}")
