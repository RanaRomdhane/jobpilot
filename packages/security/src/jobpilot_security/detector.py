"""Layered prompt-injection detector used for emails, job postings, scraped pages and CVs.

Layers: (1) heuristics on visible and hidden text, (2) the guard model, then callers add (3)
spotlighting when the content reaches an agent and (4) strict schema validation of agent output.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from jobpilot_security.guard import SEGMENT_CHARS, Guard, NullGuard
from jobpilot_security.heuristics import Category, combine, match_rules
from jobpilot_security.text import CleanText

DEFAULT_FLAG_THRESHOLD = 0.5
HIDDEN_PATTERN_BOOST = 0.15  # instructions hidden from the human reader are worse


class Layer(StrEnum):
    HEURISTIC = "heuristic"
    GUARD = "guard"
    MODEL = "model"  # the task model itself reported a manipulation attempt


class InjectionReason(BaseModel):
    layer: Layer
    rule: str
    category: str
    weight: float = Field(ge=0, le=1)
    excerpt: str | None = None


class InjectionReport(BaseModel):
    score: float = Field(ge=0, le=1)
    flagged: bool
    reasons: list[InjectionReason] = Field(default_factory=list)

    def merged_with(self, other: InjectionReport, threshold: float) -> InjectionReport:
        reasons = [*self.reasons, *other.reasons]
        score = combine([r.weight for r in reasons])
        return InjectionReport(score=score, flagged=score >= threshold, reasons=reasons)


def heuristic_reasons(content: CleanText) -> list[InjectionReason]:
    reasons = [
        InjectionReason(
            layer=Layer.HEURISTIC,
            rule=m.rule,
            category=m.category,
            weight=m.weight,
            excerpt=m.excerpt[:160],
        )
        for m in match_rules(content.text)
    ]
    hidden = "\n".join(content.hidden)
    if hidden:
        hidden_matches = match_rules(hidden)
        # encoded data alone is not an instruction; only real patterns get the hidden-text boost
        signals = [m for m in hidden_matches if m.category != Category.ENCODING]
        for m in hidden_matches:
            boost = HIDDEN_PATTERN_BOOST if m.category != Category.ENCODING else 0.0
            reasons.append(
                InjectionReason(
                    layer=Layer.HEURISTIC,
                    rule=f"hidden.{m.rule}",
                    category=m.category,
                    weight=min(1.0, m.weight + boost),
                    excerpt=m.excerpt[:160],
                )
            )
        # hidden text alone is common in marketing mail (preheaders); weak unless it says something
        if len(hidden) > 40:
            reasons.append(
                InjectionReason(
                    layer=Layer.HEURISTIC,
                    rule="hidden.text_present",
                    category=Category.HIDDEN_CONTENT,
                    weight=0.3 if signals else 0.15,
                    excerpt=hidden[:120],
                )
            )
    if content.zero_width_count >= 3:
        reasons.append(
            InjectionReason(
                layer=Layer.HEURISTIC,
                rule="hidden.zero_width_chars",
                category=Category.HIDDEN_CONTENT,
                weight=0.4 if content.zero_width_count < 20 else 0.6,
                excerpt=f"{content.zero_width_count} zero-width characters",
            )
        )
    if content.bidi_count:
        reasons.append(
            InjectionReason(
                layer=Layer.HEURISTIC,
                rule="hidden.bidi_controls",
                category=Category.HIDDEN_CONTENT,
                weight=0.5,
                excerpt=f"{content.bidi_count} bidirectional control characters",
            )
        )
    return reasons


class InjectionDetector:
    def __init__(
        self,
        guard: Guard | None = None,
        *,
        threshold: float = DEFAULT_FLAG_THRESHOLD,
        guard_threshold: float = 0.8,
    ) -> None:
        self._guard = guard or NullGuard()
        self.threshold = threshold
        self.guard_threshold = guard_threshold

    def scan_heuristics(self, content: CleanText) -> InjectionReport:
        reasons = heuristic_reasons(content)
        score = combine([r.weight for r in reasons])
        return InjectionReport(score=score, flagged=score >= self.threshold, reasons=reasons)

    async def scan(self, content: CleanText, *, extra_text: str = "") -> InjectionReport:
        """Full scan. `extra_text` (e.g. an email subject) is scanned together with the body."""
        if extra_text:
            content = CleanText(
                text=f"{extra_text}\n{content.text}",
                hidden=content.hidden,
                zero_width_count=content.zero_width_count,
                bidi_count=content.bidi_count,
            )
        reasons = heuristic_reasons(content)
        # one guard request per item: hidden text first (where injections hide), then the start
        # of the visible text (subject included), capped to a single classifier segment
        hidden = "\n".join(content.hidden)[: SEGMENT_CHARS // 2]
        guard_input = "\n".join(p for p in (hidden, content.text) if p)[:SEGMENT_CHARS]
        guard_score = await self._guard.score(guard_input)
        if guard_score is not None and guard_score >= 0.05:
            # rescale so that the guard alone flags only above its own (calibrated) threshold
            weight = min(1.0, guard_score * self.threshold / self.guard_threshold)
            reasons.append(
                InjectionReason(
                    layer=Layer.GUARD,
                    rule="guard.score",
                    category="guard",
                    weight=round(weight, 4),
                    excerpt=f"guard probability {guard_score:.3f}",
                )
            )
        score = combine([r.weight for r in reasons])
        return InjectionReport(score=score, flagged=score >= self.threshold, reasons=reasons)

    def model_report(self, reason: str | None) -> InjectionReport:
        """Evidence reported by the task model itself (e.g. TriageAgent's `injection_suspected`)."""
        r = InjectionReason(
            layer=Layer.MODEL,
            rule="model.reported",
            category="model",
            weight=0.6,
            excerpt=(reason or "")[:160] or None,
        )
        return InjectionReport(score=0.6, flagged=self.threshold <= 0.6, reasons=[r])


def _rule_weights() -> dict[str, tuple[float, str]]:
    from jobpilot_security.heuristics import RULES

    return {r.id: (r.weight, r.category.value) for r in RULES}


def reweigh(report: InjectionReport, threshold: float = DEFAULT_FLAG_THRESHOLD) -> InjectionReport:
    """Recompute a stored report with the *current* heuristic weights (after a recalibration).

    Bodies are not stored, but every reason keeps its rule id, so heuristic evidence can be
    re-scored. Guard and model evidence is kept as recorded.
    """
    base = _rule_weights()
    base["encoding.base64_blob"] = (0.1, Category.ENCODING.value)
    reasons: list[InjectionReason] = []
    hidden_signal = any(
        r.layer == Layer.HEURISTIC
        and r.rule.startswith("hidden.")
        and r.category not in (Category.ENCODING, Category.HIDDEN_CONTENT)
        for r in report.reasons
    )
    for r in report.reasons:
        weight = r.weight
        if r.layer == Layer.HEURISTIC:
            rule = r.rule
            prefix = ""
            for p in ("hidden.", "decoded."):
                if rule.startswith(p) and rule[len(p) :] in base:
                    prefix, rule = p, rule[len(p) :]
            if rule in base:
                weight, category = base[rule]
                if prefix == "decoded.":
                    weight = min(1.0, weight + 0.1)
                elif prefix == "hidden." and category != Category.ENCODING:
                    weight = min(1.0, weight + HIDDEN_PATTERN_BOOST)
            elif r.rule == "hidden.text_present":
                weight = 0.3 if hidden_signal else 0.15
        reasons.append(r.model_copy(update={"weight": weight}))
    score = combine([r.weight for r in reasons])
    return InjectionReport(score=score, flagged=score >= threshold, reasons=reasons)
