"""Figures: the ablation heatmap and the block-rate bar chart with bootstrap CIs. Reads only the result CSVs."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import colormaps  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402

from app.types import LAYERS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

LAYER_NAMES = {
    "input_guard": "input guard",
    "allowlist": "allowlist",
    "ast_rules": "AST rules",
    "cost_gate": "EXPLAIN gate",
    "sandbox": "sandbox",
    "output_guard": "output guard",
}
ATTACK_ROWS = [
    ("destructive_escalation", "destructive / escalation"),
    ("exfiltration", "exfiltration"),
    ("prompt_injection", "prompt injection"),
    ("resource_abuse", "resource abuse"),
    ("all_attacks", "all attacks"),
]
# (group title, [(config, two-line label)])
COLUMN_GROUPS = [
    (None, [("none", "no\nguards")]),
    ("single layer alone", [(f"only:{l}", f"only\n{LAYER_NAMES[l]}") for l in LAYERS]),
    ("full pipeline minus one layer", [(f"without:{l}", f"all but\n{LAYER_NAMES[l]}") for l in LAYERS]),
    (None, [("full", "all six\nlayers")]),
]
SPLIT_ORDER = ("dev", "heldout")


def _resolve(path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def available_splits(results_dir, splits=SPLIT_ORDER) -> list[str]:
    rd = _resolve(results_dir)
    return [s for s in splits if (rd / f"ablation_{s}.csv").exists()]


def _lookup(df: pd.DataFrame) -> dict[tuple[str, str], pd.Series]:
    return {(r["config"], r["category"]): r for _, r in df.iterrows()}


def _text_color(cmap, norm, v: float) -> str:
    r, g, b, _ = cmap(norm(v))
    return "white" if 0.299 * r + 0.587 * g + 0.114 * b < 0.55 else "#1a1a1a"


def _draw_panel(ax, df: pd.DataFrame, split: str) -> None:
    look = _lookup(df)
    cfgs: list[str | None] = []
    labels: list[str] = []
    group_spans: list[tuple[str | None, int, int]] = []
    for gtitle, cols in COLUMN_GROUPS:
        if cfgs:
            cfgs.append(None)  # gap column
            labels.append("")
        start = len(cfgs)
        for cfg, lab in cols:
            cfgs.append(cfg)
            labels.append(lab)
        group_spans.append((gtitle, start, len(cfgs) - 1))
    ncol = len(cfgs)
    nrow = len(ATTACK_ROWS) + 2  # attack rows, gap row, benign row

    atk = np.full((nrow, ncol), np.nan)
    ben = np.full((nrow, ncol), np.nan)
    for j, cfg in enumerate(cfgs):
        if cfg is None:
            continue
        for i, (cat, _) in enumerate(ATTACK_ROWS):
            r = look.get((cfg, cat))
            if r is not None and pd.notna(r["block_rate"]):
                atk[i, j] = 100 * r["block_rate"]
        r = look.get((cfg, "benign"))
        if r is not None and pd.notna(r["false_block_rate"]):
            ben[nrow - 1, j] = 100 * r["false_block_rate"]

    x = np.arange(ncol + 1)
    y = np.arange(nrow + 1)
    norm = Normalize(0, 100)
    cm_a, cm_b = colormaps["Blues"], colormaps["Oranges"]
    ax.pcolormesh(x, y, np.ma.masked_invalid(atk), cmap=cm_a, norm=norm, edgecolors="white", linewidth=1.2)
    ax.pcolormesh(x, y, np.ma.masked_invalid(ben), cmap=cm_b, norm=norm, edgecolors="white", linewidth=1.2)
    ax.set_xlim(0, ncol)
    ax.set_ylim(nrow, 0)

    for i in range(nrow):
        for j in range(ncol):
            for mat, cm in ((atk, cm_a), (ben, cm_b)):
                v = mat[i, j]
                if not np.isnan(v):
                    ax.text(j + 0.5, i + 0.5, f"{v:.0f}", ha="center", va="center", fontsize=10,
                            color=_text_color(cm, norm, v), fontweight="bold" if i == 4 else "normal")

    n_of = {cat: (int(look[("full", cat)]["n"]) if ("full", cat) in look else 0) for cat, _ in ATTACK_ROWS}
    ylabels = [f"{lab} (n={n_of[cat]})" for cat, lab in ATTACK_ROWS]
    n_ben = int(look[("full", "benign")]["n"]) if ("full", "benign") in look else 0
    ax.set_yticks([i + 0.5 for i in range(len(ATTACK_ROWS))] + [nrow - 0.5])
    ax.set_yticklabels(ylabels + [f"benign: false-block rate (n={n_ben})"], fontsize=10)
    ax.set_xticks([j + 0.5 for j in range(ncol)])
    ax.set_xticklabels(labels, fontsize=9)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    for gtitle, a, b in group_spans:
        if gtitle:
            ax.annotate("", xy=(a + 0.05, -0.18), xytext=(b + 0.95, -0.18), xycoords="data",
                        arrowprops=dict(arrowstyle="-", color="#555555", lw=0.9), annotation_clip=False)
            ax.text((a + b + 1) / 2, -0.32, gtitle, ha="center", va="bottom", fontsize=10, color="#333333")
    n_att = n_of.get("all_attacks", 0)
    ax.set_title(f"{split} split (n attacks = {n_att}, n benign = {n_ben})", fontsize=12, loc="left", pad=26)


def make_ablation_figure(results_dir="eval/results", out="docs/figures/ablation.png", splits=SPLIT_ORDER) -> Path:
    rd = _resolve(results_dir)
    present = available_splits(rd, splits)
    if not present:
        raise FileNotFoundError(f"no ablation_<split>.csv found in {rd}")
    fig, axes = plt.subplots(len(present), 1, figsize=(14, 4.3 * len(present) + 0.9), squeeze=False)
    for ax, split in zip(axes[:, 0], present):
        _draw_panel(ax, pd.read_csv(rd / f"ablation_{split}.csv"), split)
    fig.text(
        0.01, 0.005,
        "Cells: % of attacks blocked (blue; higher is better) and % of benign queries wrongly blocked (orange; lower is better).\n"
        "An attack counts as blocked when it did not achieve its goal. Rates over attacks that already succeed with all guards off "
        "(\"live\" attacks) are in ablation_<split>.csv.",
        fontsize=8.5, color="#444444", va="bottom", ha="left",
    )
    fig.tight_layout(rect=(0, 0.045, 1, 1), h_pad=3.0)
    outp = _resolve(out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(outp, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return outp


def make_ci_figure(results_dir="eval/results", out="docs/figures/block_rates_ci.png", splits=SPLIT_ORDER) -> Path:
    """Full-pipeline block rate per category with bootstrap CIs, splits side by side, plus the false-block rate."""
    rd = _resolve(results_dir)
    present = available_splits(rd, splits)
    if not present:
        raise FileNotFoundError(f"no ablation_<split>.csv found in {rd}")
    cats = [(c, l) for c, l in ATTACK_ROWS] + [("benign", "benign\n(false-block rate)")]
    fig, ax = plt.subplots(figsize=(11, 4.8))
    width = 0.8 / len(present)
    shades = np.linspace(0.45, 0.85, len(present))
    for k, split in enumerate(present):
        look = _lookup(pd.read_csv(rd / f"ablation_{split}.csv"))
        for i, (cat, _) in enumerate(cats):
            r = look.get(("full", cat))
            if r is None:
                continue
            if cat == "benign":
                rate, lo, hi, cm = r["false_block_rate"], r["ci_lo"], r["ci_hi"], colormaps["Oranges"]
            else:
                rate, lo, hi, cm = r["block_rate"], r["ci_lo"], r["ci_hi"], colormaps["Blues"]
            if pd.isna(rate):
                continue
            xpos = i + (k - (len(present) - 1) / 2) * width
            ax.bar(xpos, 100 * rate, width * 0.92, color=cm(shades[k]), label=split if i == 0 else None)
            ax.errorbar(xpos, 100 * rate, yerr=[[100 * (rate - lo)], [100 * (hi - rate)]], color="#222222", capsize=3, lw=1)
            ax.text(xpos, 100 * hi + 1.5, f"{100 * rate:.0f}%\nn={int(r['n'])}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(range(len(cats)))
    ax.set_xticklabels([l for _, l in cats], fontsize=9)
    ax.set_ylim(0, 118)
    ax.set_ylabel("% (95% bootstrap CI)")
    ax.set_title("Full pipeline: attacks blocked (blue, higher is better) and benign wrongly blocked (orange, lower is better)",
                 fontsize=11, loc="left")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(title="split (lighter to darker)", frameon=False, loc="lower left")
    fig.tight_layout()
    outp = _resolve(out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(outp, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return outp


if __name__ == "__main__":
    print(make_ablation_figure())
    print(make_ci_figure())
