"""USER-scoped native Skill inventory and authenticated cursor support."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.gateway.nexus_receiver.models import ReceiverSkillListItem


class ReceiverSkillCursorInvalid(ValueError):
    pass


class ReceiverSkillCursorExpired(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReceiverSkillCursor:
    principal_subject: str
    user_id: str
    normalized_query: str
    catalog_revision: str
    last_sort_key: str


class HmacReceiverSkillCursorCodec:
    """Compact HMAC cursor bound to every authorization and snapshot input."""

    def __init__(
        self,
        signing_key: bytes,
        *,
        clock: Callable[[], datetime] | None = None,
        ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        if len(signing_key) < 32 or ttl <= timedelta(0):
            raise ValueError("receiver Skill cursor key or TTL is invalid")
        self._key = bytes(signing_key)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl = ttl

    @staticmethod
    def _b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _unb64(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def encode(self, cursor: ReceiverSkillCursor) -> str:
        payload = {
            "e": int((self._clock() + self._ttl).timestamp()),
            "k": cursor.last_sort_key,
            "p": self._binding("principal", cursor.principal_subject),
            "q": self._binding("query", cursor.normalized_query),
            "r": self._binding("revision", cursor.catalog_revision),
            "u": self._binding("user", cursor.user_id),
            "v": 1,
        }
        encoded = self._b64(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode())
        signature = self._b64(hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def _binding(self, label: str, value: str) -> str:
        return self._b64(hmac.new(self._key, f"{label}\0{value}".encode(), hashlib.sha256).digest()[:18])

    def decode(self, value: str) -> ReceiverSkillCursor:
        try:
            encoded, signature = value.split(".", 1)
            expected = hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(self._unb64(signature), expected):
                raise ValueError
            payload = json.loads(self._unb64(encoded))
            if not isinstance(payload, dict) or payload.get("v") != 1:
                raise ValueError
            expires_at = payload["e"]
            strings = [payload[key] for key in ("k", "p", "q", "r", "u")]
            if isinstance(expires_at, bool) or not isinstance(expires_at, int) or any(not isinstance(item, str) for item in strings):
                raise ValueError
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            raise ReceiverSkillCursorInvalid() from None
        if expires_at <= int(self._clock().timestamp()):
            raise ReceiverSkillCursorExpired()
        return ReceiverSkillCursor(
            principal_subject=payload["p"],
            user_id=payload["u"],
            normalized_query=payload["q"],
            catalog_revision=payload["r"],
            last_sort_key=payload["k"],
        )

    def matches_request(self, cursor: ReceiverSkillCursor, *, principal_subject: str, user_id: str, normalized_query: str) -> bool:
        return (
            hmac.compare_digest(cursor.principal_subject, self._binding("principal", principal_subject))
            and hmac.compare_digest(cursor.user_id, self._binding("user", user_id))
            and hmac.compare_digest(cursor.normalized_query, self._binding("query", normalized_query))
        )

    def matches_revision(self, cursor: ReceiverSkillCursor, revision: str) -> bool:
        return hmac.compare_digest(cursor.catalog_revision, self._binding("revision", revision))


class ReceiverInventoryStorage(Protocol):
    def list_receiver_inventory(self) -> list[dict[str, Any]]: ...


class UserScopedReceiverSkillInventory:
    """Reads receiver-owned metadata through a native USER storage adapter."""

    def __init__(self, storage_factory: Callable[[str], ReceiverInventoryStorage]) -> None:
        self._storage_factory = storage_factory

    async def list_user_skills(self, *, user_id: str) -> list[ReceiverSkillListItem]:
        _revision, items = await self.snapshot(user_id=user_id)
        return items

    async def catalog_revision(self, *, user_id: str) -> str:
        revision, _items = await self.snapshot(user_id=user_id)
        return revision

    async def snapshot(self, *, user_id: str) -> tuple[str, list[ReceiverSkillListItem]]:
        storage = await asyncio.to_thread(self._storage_factory, user_id)
        rows = await asyncio.to_thread(storage.list_receiver_inventory)
        items = [ReceiverSkillListItem.model_validate(row) for row in rows]
        canonical = [item.model_dump(mode="json", by_alias=True) for item in sorted(items, key=lambda item: item.runtime_skill_name.casefold())]
        for item in canonical:
            item.pop("observedAt", None)
        revision = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return revision, items
