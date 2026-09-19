"""Private-review-only fused authority activation foundation.

Production defaults to LEGACY. This module owns authority decisions only; it does
not own retrieval, grouping, translation/style authority, receipts, or public
publishing. Before the first fused receipt, unsafe conditions fail open to legacy.
After any fused receipt, legacy fallback is forbidden to avoid duplicate delivery.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .final_edit_capture import FINAL_EDIT_PROVENANCE, fingerprint
from .fused_private_review_delivery import (
    DELIVERY_UNIT_KINDS,
    FUSED_DELIVERY_PLAN_VERSION,
    TELEGRAM_CAPTION_LIMIT,
    FusedDeliveryUnit,
    FusedPrivateReviewDeliveryPlan,
)
from .media_delivery import MediaDeliveryLedger
from .models import Draft, MediaItem, Update
from .telegram import TELEGRAM_TEXT_LIMIT, draft_keyboard, split_telegram_text

AUTHORITY_VERSION = 1
AUTHORITY_ENV = "FUSED_PRIVATE_REVIEW_AUTHORITY"
CANARY_PERCENT_ENV = "FUSED_PRIVATE_REVIEW_CANARY_PERCENT"
KILL_SWITCH_ENV = "FUSED_PRIVATE_REVIEW_KILL_SWITCH"

LEGACY = "LEGACY"
SHADOW = "SHADOW"
CANARY = "CANARY"
ON = "ON"
AUTHORITY_MODES = frozenset({LEGACY, SHADOW, CANARY, ON})

EXECUTABLE_READINESS = frozenset({"READY_TO_PRESENT", "READY_WITH_WARNINGS"})
SAFE_WARNING_CATEGORIES = frozenset({
    "TELEGRAM_TEXT_SPLIT_REQUIRED",
    "CAPTION_OVERFLOW_TO_TEXT",
    "EXACT_MEDIA_DEDUPE_COMPAT",
    "UNSUPPORTED_ALBUM_COMBINATION_SPLIT",
    "BODY_RESOLUTION_REQUIRED_BEFORE_AUTHORITY",
})
BLOCKED_WARNING_CATEGORIES = frozenset({
    "PARTIAL_COVERAGE",
    "MEDIA_INCOMPLETE",
    "CONFLICT_UNRESOLVED",
    "TRANSLATION_FIDELITY_NOT_READY",
})


def parse_authority_mode(value: object) -> str:
    """Strict parser: missing or malformed values are always LEGACY."""
    raw = str(value or "").strip().casefold()
    return {
        "off": LEGACY,
        "false": LEGACY,
        "0": LEGACY,
        "legacy": LEGACY,
        "shadow": SHADOW,
        "canary": CANARY,
        "on": ON,
        "true": ON,
        "1": ON,
    }.get(raw, LEGACY)


def parse_kill_switch(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def parse_canary_percent(value: object) -> int:
    try:
        parsed = int(str(value or "0").strip())
    except (TypeError, ValueError):
        return 0
    return parsed if 0 <= parsed <= 100 else 0


@dataclass(frozen=True, slots=True)
class FusedAuthorityConfig:
    mode: str = LEGACY
    canary_percent: int = 0
    kill_switch: bool = False
    version: int = AUTHORITY_VERSION

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "FusedAuthorityConfig":
        env = os.environ if environ is None else environ
        return cls(
            mode=parse_authority_mode(env.get(AUTHORITY_ENV)),
            canary_percent=parse_canary_percent(env.get(CANARY_PERCENT_ENV)),
            kill_switch=parse_kill_switch(env.get(KILL_SWITCH_ENV)),
        )


@dataclass(frozen=True, slots=True)
class ResolvedBody:
    body: str
    source: str
    reference: str
    fingerprint: str
    draft_id: str = ""


@dataclass(frozen=True, slots=True)
class ReceiptSnapshot:
    text_receipts_present: bool = False
    media_receipts_present: bool = False

    @property
    def any(self) -> bool:
        return self.text_receipts_present or self.media_receipts_present


@dataclass(frozen=True, slots=True)
class FusedAuthorityDecision:
    authority_mode: str
    selected_path: str
    eligible: bool
    reason: str
    plan_id: str = ""
    package_id: str = ""
    canary_selected: bool = False
    fallback_reason: str = ""
    receipts_present: bool = False
    execution_started: bool = False
    execution_completed: bool = False
    version: int = AUTHORITY_VERSION

    def privacy_safe_metadata(self) -> dict[str, Any]:
        return {
            "authority_version": self.version,
            "authority_mode": self.authority_mode,
            "selected_path": self.selected_path,
            "eligible": self.eligible,
            "reason": self.reason,
            "plan_id": self.plan_id,
            "package_id": self.package_id,
            "canary_selected": self.canary_selected,
            "fallback_reason": self.fallback_reason,
            "receipts_present": self.receipts_present,
            "execution_started": self.execution_started,
            "execution_completed": self.execution_completed,
        }


@dataclass(frozen=True, slots=True)
class FusedExecutionResult:
    completed: bool
    receipts_present: bool
    sent_unit_ids: tuple[str, ...] = ()
    failed_unit_id: str = ""
    reason: str = ""


def deterministic_canary_selected(plan_id: str, package_id: str, percentage: int) -> bool:
    percentage = parse_canary_percent(percentage)
    if percentage <= 0:
        return False
    if percentage >= 100:
        return True
    digest = hashlib.sha256(
        f"fused-authority-canary-v1\x1f{plan_id}\x1f{package_id}".encode("utf-8")
    ).hexdigest()
    return int(digest[:8], 16) % 10_000 < percentage * 100


def _drafts_by_id(state: Any) -> dict[str, Draft]:
    result: dict[str, Draft] = {}
    raw = getattr(state, "data", {}).get("drafts", {})
    if not isinstance(raw, dict):
        return result
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        try:
            result[str(key)] = Draft.from_dict(value)
        except (TypeError, ValueError):
            continue
    return result


def authoritative_draft_fingerprint(body: str) -> str:
    return fingerprint("authoritative-review-draft-v1", str(body or ""))


class FusedBodyResolver:
    """Resolve only canonical bodies; shadow style is never promoted here."""

    def __init__(self, state: Any, final_edit_store: Any = None):
        self.state = state
        self.final_edit_store = final_edit_store

    def resolve(self, plan: FusedPrivateReviewDeliveryPlan, unit: FusedDeliveryUnit) -> ResolvedBody | None:
        drafts = _drafts_by_id(self.state)
        draft_ids = tuple(dict.fromkeys(
            tuple(unit.fallback_text_refs) + tuple(plan.text_authority.fallback_references)
        ))

        # Final Edit is authoritative only when its canonical store, active record,
        # Draft/update correlation and both body fingerprints still agree.
        if plan.text_authority.final_edit_confirmed and self.final_edit_store is not None:
            expected_id = str(plan.text_authority.preferred_reference or "")
            for draft_id in draft_ids:
                draft = drafts.get(draft_id)
                if draft is None:
                    continue
                record = self.final_edit_store.latest_active(draft_id)
                if record is None or str(record.final_edit_id) != expected_id:
                    continue
                if str(record.update_id) != str(draft.update_id):
                    continue
                if str(record.edit_provenance) != FINAL_EDIT_PROVENANCE:
                    continue
                if not bool(record.active) or bool(record.revoked):
                    continue
                draft_fp = authoritative_draft_fingerprint(draft.caption)
                if str(record.authoritative_review_draft_fingerprint) != draft_fp:
                    continue
                body = str(self.final_edit_store.final_body(record.final_edit_id) or "")
                if not body:
                    continue
                body_fp = fingerprint("final-user-edit-v1", body)
                if body_fp != str(record.final_user_edit_fingerprint):
                    continue
                return ResolvedBody(body, "confirmed_final_edit", record.final_edit_id, body_fp, draft_id)

        # Direct/Channel style candidates remain shadow-only. The safe authority
        # fallback is the current canonical review Draft, never a reconstructed
        # style body from historical/corpus data.
        for draft_id in draft_ids:
            draft = drafts.get(draft_id)
            if draft is None or not str(draft.caption or ""):
                continue
            body = str(draft.caption)
            return ResolvedBody(
                body,
                "authoritative_review_draft",
                draft_id,
                authoritative_draft_fingerprint(body),
                draft_id,
            )
        return None


def canonical_media_index(updates: Iterable[Update]) -> dict[str, tuple[Update, MediaItem]]:
    result: dict[str, tuple[Update, MediaItem]] = {}
    for update in updates:
        for item in update.media:
            result.setdefault(MediaDeliveryLedger.url_identity(item), (update, item))
    return result


def validate_plan_for_authority(
    plan: FusedPrivateReviewDeliveryPlan,
    *,
    resolver: FusedBodyResolver | None,
    private_review_chat_valid: bool,
    receipt_stores_healthy: bool,
) -> tuple[bool, str]:
    if plan.version != FUSED_DELIVERY_PLAN_VERSION:
        return False, "UNKNOWN_PLAN_VERSION"
    if not private_review_chat_valid:
        return False, "PRIVATE_REVIEW_CHAT_MISSING"
    if not receipt_stores_healthy:
        return False, "RECEIPT_STORE_UNAVAILABLE"
    if plan.readiness not in EXECUTABLE_READINESS:
        return False, f"READINESS_{plan.readiness}"
    warnings = set(plan.warnings)
    if warnings & BLOCKED_WARNING_CATEGORIES:
        return False, "SAFETY_WARNING_PRESENT"
    if plan.readiness == "READY_WITH_WARNINGS" and warnings - SAFE_WARNING_CATEGORIES:
        return False, "UNRESOLVED_WARNING"
    control_count = 0
    previous_order = -1
    for unit in plan.units:
        if unit.kind not in DELIVERY_UNIT_KINDS:
            return False, "UNSUPPORTED_UNIT"
        if unit.order_index <= previous_order:
            return False, "UNIT_ORDER_INVALID"
        previous_order = unit.order_index
        if unit.kind == "media_album" and not (2 <= len(unit.media_refs) <= 10):
            return False, "TELEGRAM_ALBUM_LIMIT"
        if unit.kind in {"single_photo", "single_video", "standalone_media"} and len(unit.media_refs) != 1:
            return False, "MEDIA_UNIT_CARDINALITY"
        if unit.kind == "caption" and unit.telegram_part_count > 1:
            return False, "CAPTION_SPLIT_INVALID"
        if unit.kind in {"text", "continuation_text", "caption"}:
            resolved = resolver.resolve(plan, unit) if resolver is not None else None
            if resolved is None:
                return False, "BODY_UNRESOLVABLE"
            if unit.kind == "caption" and len(resolved.body) > TELEGRAM_CAPTION_LIMIT:
                return False, "CAPTION_LIMIT"
            if any(len(part) > TELEGRAM_TEXT_LIMIT for part in split_telegram_text(resolved.body)):
                return False, "TELEGRAM_TEXT_LIMIT"
        if unit.kind == "review_controls":
            control_count += 1
    if control_count > 1:
        return False, "REVIEW_CONTROLS_DUPLICATED"
    forward = plan.future_forward_action
    if forward.enabled or forward.auto_forward or forward.public_default or forward.target_chat_configured:
        return False, "FORWARD_OR_PUBLIC_TARGET_ENABLED"
    return True, "ELIGIBLE"


class FusedPrivateReviewAuthorityController:
    """Deterministic LEGACY/SHADOW/CANARY/ON decision engine."""

    def __init__(self, config: FusedAuthorityConfig | None = None):
        self.config = config or FusedAuthorityConfig()

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "FusedPrivateReviewAuthorityController":
        return cls(FusedAuthorityConfig.from_environment(environ))

    def decide(
        self,
        plan: FusedPrivateReviewDeliveryPlan | None,
        *,
        receipt_snapshot: ReceiptSnapshot = ReceiptSnapshot(),
        resolver: FusedBodyResolver | None = None,
        private_review_chat_valid: bool = True,
        receipt_stores_healthy: bool = True,
    ) -> FusedAuthorityDecision:
        mode = self.config.mode
        if plan is None:
            return FusedAuthorityDecision(
                mode, LEGACY, False, "PLAN_UNAVAILABLE",
                fallback_reason="PLAN_UNAVAILABLE", receipts_present=receipt_snapshot.any,
            )
        common = {
            "plan_id": plan.plan_id,
            "package_id": plan.package_id,
            "receipts_present": receipt_snapshot.any,
        }

        # Post-receipt: never fall back to a full legacy resend.
        if receipt_snapshot.any:
            if self.config.kill_switch:
                return FusedAuthorityDecision(
                    mode, "FUSED_RESUME_REQUIRED", False, "KILL_SWITCH_AFTER_RECEIPT",
                    fallback_reason="MANUAL_OR_RECEIPT_AWARE_FUSED_RECOVERY", **common,
                )
            return FusedAuthorityDecision(mode, "FUSED_RESUME", True, "FUSED_RECEIPT_PRESENT", **common)

        # Pre-send: kill switch or any safety failure is allowed to fail open.
        if self.config.kill_switch:
            return FusedAuthorityDecision(
                mode, LEGACY, False, "KILL_SWITCH", fallback_reason="KILL_SWITCH", **common,
            )
        if mode == LEGACY:
            return FusedAuthorityDecision(mode, LEGACY, False, "AUTHORITY_OFF", **common)

        eligible, reason = validate_plan_for_authority(
            plan,
            resolver=resolver,
            private_review_chat_valid=private_review_chat_valid,
            receipt_stores_healthy=receipt_stores_healthy,
        )
        if not eligible:
            return FusedAuthorityDecision(
                mode, LEGACY, False, reason, fallback_reason=reason, **common,
            )
        if mode == SHADOW:
            return FusedAuthorityDecision(mode, LEGACY, True, "SHADOW_ONLY", **common)
        if mode == CANARY:
            selected = deterministic_canary_selected(
                plan.plan_id, plan.package_id, self.config.canary_percent
            )
            return FusedAuthorityDecision(
                mode,
                "FUSED" if selected else LEGACY,
                True,
                "CANARY_SELECTED" if selected else "CANARY_NOT_SELECTED",
                canary_selected=selected,
                fallback_reason="" if selected else "CANARY_NOT_SELECTED",
                **common,
            )
        if mode == ON:
            return FusedAuthorityDecision(mode, "FUSED", True, "AUTHORITY_ON", **common)
        return FusedAuthorityDecision(
            LEGACY, LEGACY, False, "MALFORMED_MODE", fallback_reason="MALFORMED_MODE", **common,
        )


class FusedReceiptProbe:
    """Read MessageDeliveryStore/MediaDeliveryLedger without a third receipt store."""

    def __init__(self, message_delivery_store: Any = None, media_delivery_ledger: Any = None):
        self.message_delivery_store = message_delivery_store
        self.media_delivery_ledger = media_delivery_ledger

    @staticmethod
    def delivery_key(plan: FusedPrivateReviewDeliveryPlan, unit: FusedDeliveryUnit) -> str:
        return f"fused:{plan.plan_id}:{unit.unit_id}"

    def snapshot(
        self,
        plan: FusedPrivateReviewDeliveryPlan,
        *,
        resolver: FusedBodyResolver | None = None,
        media_index: Mapping[str, tuple[Update, MediaItem]] | None = None,
    ) -> ReceiptSnapshot:
        text_present = False
        media_present = False
        if self.message_delivery_store is not None and resolver is not None:
            for unit in plan.units:
                if unit.kind not in {"text", "continuation_text", "caption", "review_controls"}:
                    continue
                if unit.kind == "review_controls":
                    continue
                resolved = resolver.resolve(plan, unit)
                if resolved is None:
                    continue
                key = self.delivery_key(plan, unit)
                for index, part in enumerate(split_telegram_text(resolved.body)):
                    if self.message_delivery_store.confirmed_message_id(key, index, part) is not None:
                        text_present = True
                        break
                if text_present:
                    break
        if self.media_delivery_ledger is not None and media_index is not None:
            for unit in plan.units:
                for media_ref in unit.media_refs:
                    pair = media_index.get(media_ref)
                    if pair is None:
                        continue
                    _update, item = pair
                    if self.media_delivery_ledger.any_recent(self.media_delivery_ledger.identities_for(item)):
                        media_present = True
                        break
                if media_present:
                    break
        return ReceiptSnapshot(text_present, media_present)


class FusedUnitExecutor:
    """Executor adapter over existing private Telegram/media helpers.

    No chat-id parameter exists here, so it cannot select a public/arbitrary target.
    Text always goes through TelegramBot.send_message and its MessageDeliveryStore.
    Media goes through the application's existing private media delivery method,
    which owns MediaDeliveryLedger/retry/cache semantics.
    """

    def __init__(
        self,
        *,
        telegram: Any,
        resolver: FusedBodyResolver,
        updates: Sequence[Update],
        send_media_update: Any,
        receipt_probe: FusedReceiptProbe,
    ):
        self.telegram = telegram
        self.resolver = resolver
        self.updates = tuple(updates)
        self.send_media_update = send_media_update
        self.receipt_probe = receipt_probe
        self.media_index = canonical_media_index(self.updates)
        self.updates_by_id = {item.id: item for item in self.updates}

    def _transient_media_update(self, unit: FusedDeliveryUnit) -> Update:
        pairs = []
        for ref in unit.media_refs:
            pair = self.media_index.get(ref)
            if pair is None:
                raise ValueError("unresolvable canonical media reference")
            pairs.append(pair)
        if not pairs:
            raise ValueError("empty media unit")
        transient = Update.from_dict(pairs[0][0].to_dict())
        transient.media = [pair[1] for pair in pairs]
        return transient

    async def execute(self, plan: FusedPrivateReviewDeliveryPlan) -> FusedExecutionResult:
        sent: list[str] = []
        if sum(unit.kind == "review_controls" for unit in plan.units) > 1:
            return FusedExecutionResult(False, False, reason="CONTROLS_DUPLICATED")

        for unit in plan.units:
            before = self.receipt_probe.snapshot(
                plan, resolver=self.resolver, media_index=self.media_index
            )
            try:
                if unit.kind in {"text", "continuation_text", "caption"}:
                    resolved = self.resolver.resolve(plan, unit)
                    if resolved is None:
                        return FusedExecutionResult(False, before.any, tuple(sent), unit.unit_id, "BODY_UNRESOLVABLE")
                    # Captions remain separate text in this foundation. Existing
                    # transport has no caption-specific text receipt owner; attaching
                    # silently would bypass MessageDeliveryStore.
                    self.telegram.send_message(
                        resolved.body,
                        delivery_key=self.receipt_probe.delivery_key(plan, unit),
                    )
                elif unit.kind in {"media_album", "single_photo", "single_video", "standalone_media"}:
                    result = self.send_media_update(self._transient_media_update(unit))
                    if hasattr(result, "__await__"):
                        result = await result
                    if result is False:
                        after = self.receipt_probe.snapshot(plan, resolver=self.resolver, media_index=self.media_index)
                        return FusedExecutionResult(False, after.any, tuple(sent), unit.unit_id, "MEDIA_SEND_FAILED")
                elif unit.kind == "review_controls":
                    draft_id = next((ref for ref in plan.text_authority.fallback_references if ref), "")
                    if not draft_id:
                        return FusedExecutionResult(False, before.any, tuple(sent), unit.unit_id, "CONTROL_DRAFT_MISSING")
                    self.telegram.send_message(
                        "کنترل‌های همین پیش‌نویس:",
                        reply_markup=draft_keyboard(draft_id),
                        delivery_key=self.receipt_probe.delivery_key(plan, unit),
                    )
                else:
                    return FusedExecutionResult(False, before.any, tuple(sent), unit.unit_id, "UNSUPPORTED_UNIT")
            except Exception:
                after = self.receipt_probe.snapshot(plan, resolver=self.resolver, media_index=self.media_index)
                return FusedExecutionResult(False, after.any, tuple(sent), unit.unit_id, "TRANSPORT_EXCEPTION")
            sent.append(unit.unit_id)

        final = self.receipt_probe.snapshot(plan, resolver=self.resolver, media_index=self.media_index)
        return FusedExecutionResult(True, final.any, tuple(sent), reason="COMPLETED")


def runtime_authority_metadata(
    plan: FusedPrivateReviewDeliveryPlan | None,
    *,
    environ: Mapping[str, str] | None = None,
    state: Any = None,
    final_edit_store: Any = None,
    review_chat_id: object = None,
    receipt_stores_healthy: bool = True,
) -> dict[str, Any]:
    """Return privacy-safe decision metadata without executing fused transport.

    The current production wrapper calls this only after legacy delivery. That
    preserves legacy authority while validating the future switch plumbing.
    """
    controller = FusedPrivateReviewAuthorityController.from_environment(environ)
    resolver = FusedBodyResolver(state, final_edit_store) if state is not None else None
    decision = controller.decide(
        plan,
        resolver=resolver,
        private_review_chat_valid=bool(str(review_chat_id or "").strip()),
        receipt_stores_healthy=receipt_stores_healthy,
    )
    return decision.privacy_safe_metadata()
