#!/usr/bin/env python3
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any


FEATURE_KEYS = [
    "sample_count",
    "token_len",
    "has_digit",
    "digit_count",
    "ja_ratio",
    "en_ratio",
    "negation_hit",
    "distance_hit",
    "context_freq",
]
SUPPORTED_CLASSES = ["mate_positive", "mate_negative", "distance", "unknown_marker"]


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if v != v or v in {float("inf"), float("-inf")}:
        return default
    return v


def _as_num(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    return _to_float(value)


def feature_vector(row: dict[str, Any], feature_keys: list[str] | None = None) -> dict[str, float]:
    keys = feature_keys or FEATURE_KEYS
    return {k: _as_num(row.get(k)) for k in keys}


def train_centroid_model(
    rows: list[dict[str, Any]],
    *,
    label_field: str = "token_class_label",
    fallback_label_field: str = "token_class",
    min_samples: int = 1,
) -> dict[str, Any]:
    usable: list[tuple[dict[str, float], str]] = []
    keys = list(FEATURE_KEYS)
    for row in rows:
        label = str(row.get(label_field) or "").strip()
        if not label:
            label = str(row.get(fallback_label_field) or "").strip()
        if label not in SUPPORTED_CLASSES:
            continue
        usable.append((feature_vector(row, keys), label))

    if not usable:
        raise ValueError("no labeled rows for classifier")

    label_counts: Counter[str] = Counter(label for _, label in usable)
    classes = sorted([c for c, n in label_counts.items() if n >= max(1, int(min_samples))])
    if not classes:
        raise ValueError("no class met min_samples")

    mu: dict[str, float] = {}
    sigma: dict[str, float] = {}
    for k in keys:
        vals = [vec[k] for vec, _ in usable]
        m = sum(vals) / float(len(vals))
        var = sum((v - m) ** 2 for v in vals) / float(max(1, len(vals)))
        s = max(1e-6, var ** 0.5)
        mu[k] = m
        sigma[k] = s

    buckets: dict[str, list[dict[str, float]]] = defaultdict(list)
    for vec, label in usable:
        if label in classes:
            buckets[label].append(vec)

    centroids: dict[str, dict[str, float]] = {}
    priors: dict[str, float] = {}
    total = float(sum(len(v) for v in buckets.values()))
    for label in classes:
        items = buckets[label]
        if not items:
            continue
        priors[label] = len(items) / total if total > 0 else 0.0
        center: dict[str, float] = {}
        for k in keys:
            center[k] = sum((vec[k] - mu[k]) / sigma[k] for vec in items) / float(len(items))
        centroids[label] = center

    return {
        "version": 1,
        "kind": "dfpn_token_classifier",
        "algorithm": "centroid_v1",
        "feature_keys": keys,
        "classes": classes,
        "mu": mu,
        "sigma": sigma,
        "centroids": centroids,
        "priors": priors,
        "trained_samples": len(usable),
        "class_counts": dict(label_counts),
    }


def predict(model: dict[str, Any], row: dict[str, Any]) -> tuple[str, float, dict[str, float]]:
    keys = [str(k) for k in model.get("feature_keys", FEATURE_KEYS)]
    mu = {k: _to_float(v, 0.0) for k, v in dict(model.get("mu", {})).items()}
    sigma = {k: max(1e-6, _to_float(v, 1.0)) for k, v in dict(model.get("sigma", {})).items()}
    centroids = model.get("centroids", {})
    priors = {str(k): max(1e-9, _to_float(v, 0.0)) for k, v in dict(model.get("priors", {})).items()}

    vec = feature_vector(row, keys)
    z: dict[str, float] = {k: (vec[k] - mu.get(k, 0.0)) / sigma.get(k, 1.0) for k in keys}

    raw_scores: dict[str, float] = {}
    for label, center_any in dict(centroids).items():
        if not isinstance(center_any, dict):
            continue
        center = {k: _to_float(v, 0.0) for k, v in center_any.items()}
        dist2 = sum((z[k] - center.get(k, 0.0)) ** 2 for k in keys)
        prior = priors.get(label, 1e-9)
        raw_scores[label] = -dist2 + math.log(prior)

    if not raw_scores:
        return "unknown_marker", 0.0, {}

    mx = max(raw_scores.values())
    exps = {k: math.exp(v - mx) for k, v in raw_scores.items()}
    denom = sum(exps.values()) or 1.0
    probs = {k: exps[k] / denom for k in exps}
    label = max(probs.items(), key=lambda kv: kv[1])[0]
    conf = max(0.0, min(1.0, probs[label]))
    return label, conf, probs


__all__ = [
    "FEATURE_KEYS",
    "SUPPORTED_CLASSES",
    "feature_vector",
    "train_centroid_model",
    "predict",
]
