"""The prompt-injection regression suite, shipped with the package so the API can run it live."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jobpilot_security.detector import InjectionDetector
from jobpilot_security.text import CleanText, clean_plain, html_to_text


@lru_cache
def load_samples() -> dict[str, list[dict[str, Any]]]:
    raw = files("jobpilot_security").joinpath("data/injection_samples.json").read_text("utf-8")
    data: dict[str, list[dict[str, Any]]] = json.loads(raw)
    return data


def to_clean(sample: dict[str, Any]) -> CleanText:
    body = sample["body"]
    c = html_to_text(body) if sample.get("format") == "html" else clean_plain(body)
    return CleanText(
        text=f"{sample['subject']}\n{c.text}",
        hidden=c.hidden,
        zero_width_count=c.zero_width_count,
        bidi_count=c.bidi_count,
    )


@dataclass
class SuiteResult:
    tp: int
    fp: int
    fn: int
    tn: int
    misses: list[str]
    false_positives: list[str]
    by_kind: dict[str, dict[str, int]]

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0


def run_heuristic_suite(detector: InjectionDetector | None = None) -> SuiteResult:
    """Deterministic, offline run of the heuristic layer over the whole suite."""
    detector = detector or InjectionDetector()
    data = load_samples()
    misses, fps = [], []
    by_kind: dict[str, dict[str, int]] = {}
    for s in data["injections"]:
        hit = detector.scan_heuristics(to_clean(s)).flagged
        k = by_kind.setdefault(s["kind"], {"total": 0, "detected": 0})
        k["total"] += 1
        k["detected"] += hit
        if not hit:
            misses.append(s["id"])
    for s in data["benign"]:
        if detector.scan_heuristics(to_clean(s)).flagged:
            fps.append(s["id"])
    tp = len(data["injections"]) - len(misses)
    return SuiteResult(
        tp=tp,
        fp=len(fps),
        fn=len(misses),
        tn=len(data["benign"]) - len(fps),
        misses=misses,
        false_positives=fps,
        by_kind=by_kind,
    )
