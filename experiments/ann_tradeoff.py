"""Assignment 2, Q3: two small experiments behind the vector-database answer.

A. Speed vs recall: exact (brute-force) search against an HNSW graph index.
B. Filtered search: what happens to recall and latency when a query must only
   return vectors whose metadata matches a filter (for example one tenant's
   data), for three strategies a vector database can use.

Data is synthetic: 100,000 unit vectors with 384 dimensions (the size of
all-MiniLM-L6-v2 embeddings), drawn from a 64-cluster mixture in a 32-dimension
latent space and projected up, so neighbours are structured the way real
embeddings are rather than uniformly random. Latencies are per query, one query
at a time, on a single CPU thread. Absolute numbers depend on the machine; the
shape of the curves is the point.

    pip install faiss-cpu numpy matplotlib
    python experiments/ann_tradeoff.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import faiss
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT = Path(__file__).resolve().parent / "output"
N, DIM, LATENT, CLUSTERS = 100_000, 384, 32, 64
N_QUERIES, K = 500, 10
HNSW_M, EF_CONSTRUCTION = 16, 200
EF_SWEEP = [16, 32, 64, 128, 256, 512]
SELECTIVITIES = [0.5, 0.2, 0.1, 0.05, 0.01, 0.005, 0.001]
FILTER_EF = 128  # efSearch used by both HNSW strategies in experiment B
OVERFETCH = 100  # post-filtering asks the index for 10x more results than it needs

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED, GRID, BASE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"


def make_vectors(n: int, rng: np.random.Generator, centers: np.ndarray, proj: np.ndarray):
    labels = rng.integers(0, len(centers), size=n)
    latent = centers[labels] + rng.normal(scale=0.6, size=(n, LATENT))
    x = latent @ proj + rng.normal(scale=0.05, size=(n, DIM))
    x = x.astype("float32")
    faiss.normalize_L2(x)  # unit length, so inner product = cosine similarity
    return x


def recall_at_k(found: np.ndarray, truth: np.ndarray) -> float:
    hits = sum(len(set(f[f >= 0]) & set(t[t >= 0])) for f, t in zip(found, truth))
    return hits / truth.size


def timed_search(search_one, queries: np.ndarray) -> tuple[np.ndarray, float]:
    """Run one query at a time; return results and mean milliseconds per query."""
    results = []
    start = time.perf_counter()
    for q in queries:
        results.append(search_one(q[None, :]))
    elapsed = time.perf_counter() - start
    return np.vstack(results), 1000 * elapsed / len(queries)


def style(ax) -> None:
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(BASE)
    ax.tick_params(colors=MUTED, labelsize=8.5, length=0)
    ax.xaxis.label.set_color(INK2)
    ax.yaxis.label.set_color(INK2)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    rng = np.random.default_rng(42)
    centers = rng.normal(size=(CLUSTERS, LATENT))
    proj = rng.normal(scale=1 / np.sqrt(LATENT), size=(LATENT, DIM))
    xb = make_vectors(N, rng, centers, proj)
    xq = make_vectors(N_QUERIES, rng, centers, proj)

    flat = faiss.IndexFlatIP(DIM)
    flat.add(xb)
    _, truth = flat.search(xq, K)  # ground truth (multi-threaded is fine here)

    t0 = time.perf_counter()
    hnsw = faiss.IndexHNSWFlat(DIM, HNSW_M, faiss.METRIC_INNER_PRODUCT)
    hnsw.hnsw.efConstruction = EF_CONSTRUCTION
    hnsw.add(xb)
    build_s = time.perf_counter() - t0

    faiss.omp_set_num_threads(1)  # every timed search below runs on one thread

    # ---- A. exact vs HNSW ------------------------------------------------------------
    _, flat_ms = timed_search(lambda q: flat.search(q, K)[1], xq)
    sweep = []
    for ef in EF_SWEEP:
        hnsw.hnsw.efSearch = ef
        found, ms = timed_search(lambda q: hnsw.search(q, K)[1], xq)
        sweep.append({"ef_search": ef, "recall_at_10": recall_at_k(found, truth), "ms": ms})

    # ---- B. filtered search ----------------------------------------------------------
    filtered = []
    for sel in SELECTIVITIES:
        mask = rng.random(N) < sel
        ids = np.flatnonzero(mask).astype("int64")
        sub = faiss.IndexFlatIP(DIM)
        sub.add(xb[ids])
        _, sub_truth = sub.search(xq, K)
        f_truth = ids[sub_truth]  # exact top-10 among matching vectors, as global ids

        # 1) Post-filter: search the whole graph, then drop results that fail the filter.
        hnsw.hnsw.efSearch = max(FILTER_EF, OVERFETCH)

        def post_filter(q, mask=mask):
            _, cand = hnsw.search(q, OVERFETCH)
            keep = [i for i in cand[0] if i >= 0 and mask[i]][:K]
            return np.array([keep + [-1] * (K - len(keep))])

        post, post_ms = timed_search(post_filter, xq)

        # 2) Filter during traversal: the graph is walked as usual, but only matching
        #    nodes may enter the result set.
        selector = faiss.IDSelectorBatch(ids)
        params = faiss.SearchParametersHNSW()
        params.efSearch = FILTER_EF
        params.sel = selector
        inside, inside_ms = timed_search(lambda q, p=params: hnsw.search(q, K, params=p)[1], xq)

        # 3) Pre-filter, then exact scan of only the matching vectors.
        exact, exact_ms = timed_search(lambda q, s=sub, i=ids: i[s.search(q, K)[1]], xq)

        filtered.append(
            {
                "selectivity": sel,
                "matching_vectors": int(mask.sum()),
                "post_filter": {
                    "recall": recall_at_k(post, f_truth),
                    "ms": post_ms,
                    "avg_results": float((post >= 0).sum(1).mean()),
                },
                "filter_in_traversal": {
                    "recall": recall_at_k(inside, f_truth),
                    "ms": inside_ms,
                    "avg_results": float((inside >= 0).sum(1).mean()),
                },
                "exact_on_subset": {"recall": recall_at_k(exact, f_truth), "ms": exact_ms},
            }
        )

    results = {
        "setup": {
            "n": N,
            "dim": DIM,
            "queries": N_QUERIES,
            "k": K,
            "hnsw_m": HNSW_M,
            "ef_construction": EF_CONSTRUCTION,
            "hnsw_build_seconds": build_s,
            "faiss_version": faiss.__version__,
        },
        "exact_ms": flat_ms,
        "hnsw_sweep": sweep,
        "filtered": filtered,
    }
    (OUT / "results.json").write_text(json.dumps(results, indent=2))
    plot_tradeoff(results)
    plot_filtering(results)
    print(json.dumps(results, indent=2))


def plot_tradeoff(r: dict) -> None:
    fig, ax = plt.subplots(figsize=(6.4, 3.6), layout="constrained")
    ms = [p["ms"] for p in r["hnsw_sweep"]]
    rec = [p["recall_at_10"] for p in r["hnsw_sweep"]]
    ax.plot(
        ms,
        rec,
        color=BLUE,
        linewidth=2,
        marker="o",
        markersize=6,
        zorder=3,
        markeredgecolor="white",
        markeredgewidth=1.5,
        label="HNSW (M=16), ef_search swept",
    )
    for p in r["hnsw_sweep"]:
        ax.annotate(
            f"ef={p['ef_search']}",
            (p["ms"], p["recall_at_10"]),
            xytext=(5, -11),
            textcoords="offset points",
            fontsize=8,
            color=INK2,
        )
    ax.plot(
        [r["exact_ms"]],
        [1.0],
        color=ORANGE,
        marker="s",
        markersize=7,
        linestyle="none",
        markeredgecolor="white",
        markeredgewidth=1.5,
        zorder=3,
        label="Exact brute-force scan",
    )
    ax.annotate(
        f"exact: {r['exact_ms']:.1f} ms",
        (r["exact_ms"], 1.0),
        xytext=(-6, -13),
        textcoords="offset points",
        ha="right",
        fontsize=8,
        color=INK2,
    )
    ax.set_xscale("log")
    ticks = [0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10]
    ax.set_xticks(ticks, [f"{t:g}" for t in ticks])
    ax.minorticks_off()
    ax.set_ylim(0.87, 1.012)
    ax.set_xlabel("Mean latency per query, ms (log scale, 1 CPU thread)")
    ax.set_ylabel("Recall@10")
    ax.set_title(
        "A. Exact search vs HNSW on 100,000 vectors (384-d)", loc="left", fontsize=10.5, color=INK
    )
    ax.legend(frameon=False, fontsize=8, loc="lower right", labelcolor=INK)
    style(ax)
    fig.savefig(OUT / "fig_ann_tradeoff.svg")
    fig.savefig(OUT / "fig_ann_tradeoff.png", dpi=200)
    plt.close(fig)


def plot_filtering(r: dict) -> None:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.2, 3.7), layout="constrained")
    sel = [100 * f["selectivity"] for f in r["filtered"]]
    series = [
        ("post_filter", ORANGE, "Post-filter (search, then drop non-matching)"),
        ("filter_in_traversal", BLUE, "Filter inside HNSW traversal"),
        ("exact_on_subset", AQUA, "Pre-filter, then exact scan"),
    ]
    for key, color, label in series:
        rec = [f[key]["recall"] for f in r["filtered"]]
        lat = [f[key]["ms"] for f in r["filtered"]]
        kw = {
            "color": color,
            "linewidth": 2,
            "marker": "o",
            "markersize": 5.5,
            "markeredgecolor": "white",
            "markeredgewidth": 1.2,
        }
        a1.plot(sel, rec, label=label, **kw)
        a2.plot(sel, lat, label=label, **kw)
    for ax in (a1, a2):
        ax.set_xscale("log")
        ax.invert_xaxis()
        ax.set_xticks(sel, [f"{s:g}%" for s in sel])
        ax.minorticks_off()
        ax.set_xlabel("Share of vectors that match the filter")
        style(ax)
    a1.set_ylabel("Recall@10 (vs exact filtered top-10)")
    a1.set_ylim(-0.03, 1.05)
    a2.set_yscale("log")
    yt = [0.01, 0.03, 0.1, 0.3, 1, 3]
    a2.set_yticks(yt, [f"{t:g}" for t in yt])
    a2.minorticks_off()
    a2.set_ylabel("Mean latency per query, ms (log)")
    a1.set_title("B1. Recall as the filter narrows", loc="left", fontsize=10.5, color=INK)
    a2.set_title("B2. Latency as the filter narrows", loc="left", fontsize=10.5, color=INK)
    handles, labels = a1.get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="outside lower center",
        ncol=3,
        frameon=False,
        fontsize=8,
        labelcolor=INK,
    )
    fig.savefig(OUT / "fig_filtering.svg")
    fig.savefig(OUT / "fig_filtering.png", dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    main()
