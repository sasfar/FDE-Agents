"""Metrics Calculation Engine for GTA 6 Autorater Eval Harness.

Computes per-criterion mean, min, max, item range, percent agreement,
and Krippendorff's Alpha (interval) across k autorater passes.
"""
from __future__ import annotations

from typing import Any, Dict
import numpy as np
import pandas as pd


def krippendorff_alpha_interval(grades_df: pd.DataFrame) -> float:
    """Compute interval Krippendorff's Alpha on an (N items x k passes) DataFrame."""
    df = grades_df.dropna(how="all")
    n_items, k_passes = df.shape
    if k_passes <= 1 or n_items == 0:
        return float("nan")

    # Collect all valid within-item pairs
    observed_diffs = []
    all_values = []
    for _, row in df.iterrows():
        vals = row.dropna().values.astype(float)
        m = len(vals)
        if m < 2:
            continue
        all_values.extend(vals.tolist())
        # Pairwise squared differences within item
        for i in range(m):
            for j in range(i + 1, m):
                observed_diffs.append((vals[i] - vals[j]) ** 2)

    if not observed_diffs or len(all_values) < 2:
        return float("nan")

    d_o = float(np.mean(observed_diffs))
    arr = np.array(all_values, dtype=float)
    # Expected disagreement across all paired values
    n_tot = len(arr)
    d_e = float(np.var(arr) * (2.0 * n_tot / (n_tot - 1))) if n_tot > 1 else 0.0
    if d_e == 0.0:
        return 1.0 if d_o == 0.0 else 0.0
    return 1.0 - (d_o / d_e)


def compute_percent_agreement_row(row: pd.Series) -> float:
    v = row.dropna().values
    n = len(v)
    if n <= 1:
        return float("nan")
    _, counts = np.unique(v, return_counts=True)
    agreeing_pairs = sum(int(c) * (int(c) - 1) for c in counts)
    total_pairs = n * (n - 1)
    return agreeing_pairs / total_pairs


def compute_criterion_stats(grades_df: pd.DataFrame) -> Dict[str, Any]:
    n_items, k_passes = grades_df.shape
    item_min = grades_df.min(axis=1)
    item_max = grades_df.max(axis=1)
    item_range = item_max - item_min
    item_mean = grades_df.mean(axis=1)
    item_pa = grades_df.apply(compute_percent_agreement_row, axis=1)

    return {
        "N": n_items,
        "k": k_passes,
        "mean": float(np.nanmean(grades_df.values)),
        "min_rating": float(np.nanmin(grades_df.values)),
        "max_rating": float(np.nanmax(grades_df.values)),
        "max_range_report": float(item_range.max()) if k_passes > 1 else 0.0,
        "sd_report_means": float(item_mean.std(ddof=1)) if n_items > 1 else 0.0,
        "mean_pa": float(item_pa.mean()) if k_passes > 1 else 1.0,
        "alpha": krippendorff_alpha_interval(grades_df),
    }
