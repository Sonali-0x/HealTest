"""Reproducible benchmark summaries and dependency-free SVG charts."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

ARMS = ("no_healing", "heuristic", "healtest")


def _metrics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(cases)
    healed = sum(bool(case.get("healed")) for case in cases)
    attempts = [float(case.get("attempts", 0)) for case in cases]
    seconds = [float(case.get("seconds", 0)) for case in cases]
    tokens = sum(int(case.get("tokens", 0)) for case in cases)
    unsafe = sum(int(case.get("unsafe_fixes", 0)) for case in cases)
    costs = [float(case["cost"]) for case in cases if case.get("cost") is not None]
    return {
        "total": total,
        "healed": healed,
        "heal_rate": healed / total if total else 0.0,
        "avg_attempts": statistics.mean(attempts) if attempts else 0.0,
        "median_seconds": statistics.median(seconds) if seconds else 0.0,
        "unsafe_fixes": unsafe,
        "tokens": tokens,
        "tokens_per_heal": tokens / healed if healed else None,
        "cost_per_heal": sum(costs) / healed if healed and costs else None,
    }


def summarize(cases: list[dict]) -> dict:
    """Summarize arm, mutation, and failure outcomes without hiding failures."""
    by_arm: dict[str, list[dict]] = defaultdict(list)
    by_mutation: dict[str, dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for case in cases:
        arm = str(case.get("arm", "healtest"))
        mutation = str(case.get("mutation_type", "unknown"))
        by_arm[arm].append(case)
        by_mutation[mutation][arm].append(case)

    arms = {
        arm: _metrics(by_arm.get(arm, []))
        for arm in ARMS
    }
    mutation_breakdown = {
        mutation: {
            arm: _metrics(arm_cases.get(arm, []))
            for arm in ARMS
        }
        for mutation, arm_cases in sorted(by_mutation.items())
    }
    failures = [
        {
            "case_id": case.get("case_id", ""),
            "app": case.get("app", ""),
            "mutation_type": case.get("mutation_type", ""),
            "arm": case.get("arm", ""),
            "error": case.get("error", ""),
        }
        for case in cases
        if not case.get("healed")
    ]
    return {
        "arms": arms,
        "by_mutation": mutation_breakdown,
        "failures": failures,
        "cases": cases,
    }


def render_svg(summary: dict) -> str:
    """Render a compact, accessible bar chart using only the standard library."""
    arms = summary.get("arms", {})
    labels = {
        "no_healing": "No healing",
        "heuristic": "Heuristic",
        "healtest": "HealTest",
    }
    width, height = 720, 300
    chart_left, chart_top, chart_width, chart_height = 180, 34, 470, 190
    colors = {"no_healing": "#89918a", "heuristic": "#5174a2", "healtest": "#2e694d"}
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title desc" viewBox="0 0 {width} {height}">',
        "<title id=\"title\">Benchmark heal rate by strategy</title>",
        "<desc id=\"desc\">The percentage of cases healed by each strategy.</desc>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]
    for tick in range(0, 101, 25):
        y = chart_top + chart_height - (chart_height * tick / 100)
        parts.append(
            f'<line x1="{chart_left}" y1="{y:.1f}" x2="{chart_left + chart_width}" y2="{y:.1f}" stroke="#e8ebe5"/>'
        )
        parts.append(
            f'<text x="{chart_left - 12}" y="{y + 4:.1f}" text-anchor="end" font-size="12" fill="#687269">{tick}%</text>'
        )
    bar_width, gap = 82, 44
    for index, arm in enumerate(ARMS):
        metric = arms.get(arm, {})
        rate = max(0.0, min(1.0, float(metric.get("heal_rate", 0))))
        x = chart_left + 47 + index * (bar_width + gap)
        bar_height = chart_height * rate
        y = chart_top + chart_height - bar_height
        parts.append(
            f'<rect x="{x}" y="{y:.1f}" width="{bar_width}" height="{bar_height:.1f}" rx="5" fill="{colors[arm]}"/>'
        )
        parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{max(chart_top + 15, y - 8):.1f}" text-anchor="middle" font-size="12" font-weight="700" fill="#242a24">{rate:.0%}</text>'
        )
        parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{chart_top + chart_height + 24}" text-anchor="middle" font-size="12" fill="#515b52">{escape(labels[arm])}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def write_report(summary: dict, json_path: str, svg_path: str) -> None:
    json_file = Path(json_path)
    svg_file = Path(svg_path)
    json_file.parent.mkdir(parents=True, exist_ok=True)
    svg_file.parent.mkdir(parents=True, exist_ok=True)
    json_file.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    svg_file.write_text(render_svg(summary), encoding="utf-8")
