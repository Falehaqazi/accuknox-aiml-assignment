"""Task 2: fetch students' test scores from an API, compute averages and plot them.

No public API serves student test scores, so this repo ships a small one
(mock_scores_api.py). With no --url, the script starts that API on a free local
port, fetches the data over HTTP like any other client, then shuts it down.

    python task2_scores.py                                    # bundled API
    python task2_scores.py --url http://127.0.0.1:8000/api/students
"""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # draw straight to a file; no display needed
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402

from common import build_session, configure_logging, get_json  # noqa: E402
from mock_scores_api import running_server  # noqa: E402

log = logging.getLogger("scores")

USER_AGENT = "accuknox-assignment-scores/1.0 (python-requests)"

# One colour for the data; quiet greys for text, grid and axes.
BAR = "#2a78d6"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"


@dataclass
class ScoreSummary:
    exam: str
    max_score: float
    class_average: float  # mean of every usable score
    by_student: pd.Series  # mean per student, highest first
    by_subject: pd.Series  # mean per subject, highest first
    n_scores: int
    n_students: int
    n_ignored: int


def to_frame(payload: Any) -> tuple[pd.DataFrame, int]:
    """Flatten the payload to one row per (student, subject) and drop unusable scores.

    Returns the clean frame and how many scores were ignored: absent ("AB"),
    non-numeric, or outside 0..max_score.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("students"), list):
        raise ValueError("expected a JSON object with a 'students' list")
    max_score = float(payload.get("max_score", 100))

    records = []
    for position, student in enumerate(payload["students"], start=1):
        student_id = student.get("id", position)
        name = str(student.get("name") or f"Student {student_id}")
        for subject, raw in (student.get("scores") or {}).items():
            records.append(
                {"student_id": student_id, "student": name, "subject": subject, "raw": raw}
            )

    df = pd.DataFrame.from_records(records, columns=["student_id", "student", "subject", "raw"])
    df["score"] = pd.to_numeric(df["raw"], errors="coerce")
    usable = df["score"].between(0, max_score)  # NaN is never "between", so it drops out too
    for row in df.loc[~usable].itertuples():
        log.warning("Ignoring %s's %s score: %r", row.student, row.subject, row.raw)
    clean = df.loc[usable, ["student_id", "student", "subject", "score"]].reset_index(drop=True)
    return clean, int((~usable).sum())


def summarize(payload: Any) -> ScoreSummary:
    df, ignored = to_frame(payload)
    if df.empty:
        raise ValueError("the payload contains no usable scores")
    # Group on id as well as name so two students who share a name stay separate.
    by_student = (
        df.groupby(["student_id", "student"])["score"]
        .mean()
        .droplevel(0)
        .sort_values(ascending=False)
    )
    by_subject = df.groupby("subject")["score"].mean().sort_values(ascending=False)
    return ScoreSummary(
        exam=str(payload.get("exam") or "Test scores"),
        max_score=float(payload.get("max_score", 100)),
        class_average=float(df["score"].mean()),
        by_student=by_student,
        by_subject=by_subject,
        n_scores=len(df),
        n_students=int(df["student_id"].nunique()),
        n_ignored=ignored,
    )


def format_summary(s: ScoreSummary) -> str:
    ignored = f", {s.n_ignored} ignored" if s.n_ignored else ""
    lines = [
        f"{s.exam}: {s.n_scores} scores from {s.n_students} students{ignored}",
        f"Class average: {s.class_average:.2f} / {s.max_score:g}",
        "",
        f"{'Student':<18}{'Average':>8}",
        *(f"{name:<18}{avg:>8.2f}" for name, avg in s.by_student.items()),
        "",
        f"{'Subject':<18}{'Average':>8}",
        *(f"{name:<18}{avg:>8.2f}" for name, avg in s.by_subject.items()),
    ]
    return "\n".join(lines)


def _bar_panel(ax: Any, averages: pd.Series, s: ScoreSummary, title: str) -> None:
    labels = [str(label) for label in averages.index][::-1]  # barh draws bottom-up: best on top
    values = [float(v) for v in averages.values][::-1]
    rows = range(len(values))

    # The class-average line sits behind the bars; value labels get a white backing so
    # the line never runs through a number.
    ax.axvline(s.class_average, color=INK, linewidth=1, zorder=1)
    ax.barh(rows, values, height=0.56, color=BAR, zorder=2)
    for row, value in zip(rows, values):
        ax.annotate(
            f"{value:.1f}",
            xy=(value, row),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=9,
            color=INK_SECONDARY,
            zorder=3,
            bbox={"boxstyle": "square,pad=0.1", "facecolor": "white", "edgecolor": "none"},
        )

    ax.set_yticks(list(rows), labels)
    ax.set_ylim(-0.6, len(values) - 0.4)
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=8)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(BASELINE)  # the bars' shared baseline at zero
    ax.tick_params(axis="y", length=0, labelcolor=INK, labelsize=9.5)
    ax.tick_params(axis="x", length=0, labelcolor=MUTED, labelsize=8.5)


def plot(s: ScoreSummary, out_path: Path) -> Path:
    # Panels are stacked on one shared x-axis, and each panel's height is proportional
    # to its number of bars, so bars are equally thick and the average line lines up.
    fig, (top, bottom) = plt.subplots(
        2,
        1,
        figsize=(8, 1.3 + 0.36 * (len(s.by_student) + len(s.by_subject))),
        sharex=True,
        gridspec_kw={"height_ratios": [len(s.by_student), len(s.by_subject)]},
        layout="constrained",
    )
    _bar_panel(top, s.by_student, s, "Average score per student")
    _bar_panel(bottom, s.by_subject, s, "Average score per subject")
    top.set_xlim(0, s.max_score * 1.08)
    top.annotate(
        f"Class average {s.class_average:.1f}",
        xy=(s.class_average, 1),
        xycoords=("data", "axes fraction"),
        xytext=(0, 2),
        textcoords="offset points",
        ha="center",
        va="bottom",
        fontsize=8.5,
        color=INK,
    )
    bottom.set_xlabel(f"Average score (out of {s.max_score:g})", color=INK_SECONDARY, fontsize=9)
    fig.suptitle(s.exam, x=0.02, ha="left", fontsize=13, fontweight="bold", color=INK)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=200, facecolor="white")
    plt.close(fig)
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Average student test scores and plot them.")
    parser.add_argument("--url", help="scores endpoint; omit to use the bundled local API")
    parser.add_argument(
        "--out", type=Path, default=Path("output/scores_chart.png"), help="chart file"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    session = build_session(USER_AGENT)
    source = nullcontext(args.url) if args.url else running_server()
    try:
        with source as url:
            log.info("Fetching scores from %s", url)
            payload = get_json(session, url)
        summary = summarize(payload)
    except (requests.RequestException, ValueError) as exc:
        log.error("Could not load scores: %s", exc)
        return 1

    print(format_summary(summary))
    print(f"\nChart saved to {plot(summary, args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
