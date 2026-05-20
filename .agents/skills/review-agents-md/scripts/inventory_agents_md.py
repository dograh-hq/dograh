#!/usr/bin/env python3
"""Inventory AGENTS.md scopes and large uncovered subtrees.

Run from the repo root:

    python .agents/skills/review-agents-md/scripts/inventory_agents_md.py

The output is intentionally heuristic. It helps with discovery; it does not
decide by itself whether a subtree must have its own AGENTS.md.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".next",
    ".turbo",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".cursor",
    ".claude",
    ".idea",
    ".vscode",
    "dist",
    "build",
}

CODE_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
}


@dataclass(frozen=True)
class DirSummary:
    path: Path
    code_files: int
    nested_dirs: int
    has_agents_here: bool
    descendant_agents: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize AGENTS.md coverage and likely missing deeper scopes.",
    )
    parser.add_argument(
        "roots",
        nargs="*",
        default=["."],
        help="Directories to scan. Defaults to the current directory.",
    )
    parser.add_argument(
        "--hotspot-threshold",
        type=int,
        default=12,
        help="Minimum code-file count for a subtree to be listed as a hotspot.",
    )
    return parser.parse_args()


def should_skip_dir(name: str) -> bool:
    return name in IGNORE_DIRS or name.startswith(".")


def walk_dirs(root: Path):
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not should_skip_dir(d))
        yield Path(current_root), dirnames, filenames


def discover_agents(roots: list[Path]) -> list[Path]:
    found: set[Path] = set()
    for root in roots:
        for current_root, _dirnames, filenames in walk_dirs(root):
            if "AGENTS.md" in filenames:
                found.add((current_root / "AGENTS.md").resolve())
    return sorted(found)


def count_nested_dirs(root: Path) -> int:
    count = 0
    for current_root, dirnames, _filenames in walk_dirs(root):
        if Path(current_root) == root:
            count += len(dirnames)
            continue
        count += len(dirnames)
    return count


def count_code_files(root: Path) -> int:
    count = 0
    for _current_root, _dirnames, filenames in walk_dirs(root):
        for filename in filenames:
            if Path(filename).suffix in CODE_EXTENSIONS:
                count += 1
    return count


def descendant_agents(root: Path, agents: list[Path]) -> list[Path]:
    root_resolved = root.resolve()
    return [
        agent
        for agent in agents
        if agent.parent != root_resolved and root_resolved in agent.parents
    ]


def immediate_child_dirs(root: Path) -> list[Path]:
    children = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and not should_skip_dir(child.name):
            children.append(child)
    return children


def summarize_child(child: Path, agents: list[Path]) -> DirSummary:
    desc_agents = descendant_agents(child, agents)
    return DirSummary(
        path=child,
        code_files=count_code_files(child),
        nested_dirs=count_nested_dirs(child),
        has_agents_here=(child / "AGENTS.md").exists(),
        descendant_agents=len(desc_agents),
    )


def format_path(path: Path, cwd: Path) -> str:
    try:
        return str(path.resolve().relative_to(cwd))
    except ValueError:
        return str(path.resolve())


def nested_hotspots(summary: DirSummary, agents: list[Path], threshold: int) -> list[DirSummary]:
    if summary.has_agents_here:
        return []
    if summary.code_files < threshold:
        return []

    nested_summaries = [
        summarize_child(child, agents) for child in immediate_child_dirs(summary.path)
    ]
    return [
        nested
        for nested in nested_summaries
        if (
            not nested.has_agents_here
            and nested.code_files >= threshold
            and nested.descendant_agents == 0
        )
    ]


def print_scope(scope_dir: Path, agents: list[Path], cwd: Path, threshold: int) -> None:
    scope_agent = scope_dir / "AGENTS.md"
    child_agents = descendant_agents(scope_dir, agents)

    print(f"AGENTS: {format_path(scope_agent, cwd)}")

    if child_agents:
        print("  child AGENTS:")
        for agent in child_agents:
            print(f"    - {format_path(agent, cwd)}")
    else:
        print("  child AGENTS: none")

    children = immediate_child_dirs(scope_dir)
    if not children:
        print("  immediate child dirs: none")
        print()
        return

    print("  immediate child dirs:")
    summaries = [summarize_child(child, agents) for child in children]
    for summary in summaries:
        marker = "has AGENTS" if summary.has_agents_here else "no AGENTS"
        extra = ""
        if summary.descendant_agents and not summary.has_agents_here:
            extra = f", {summary.descendant_agents} deeper AGENTS"
        print(
            "    - "
            f"{format_path(summary.path, cwd)}/ -> "
            f"{summary.code_files} code files, "
            f"{summary.nested_dirs} nested dirs, "
            f"{marker}{extra}"
        )

    hotspots = [
        summary
        for summary in summaries
        if (
            not summary.has_agents_here
            and summary.code_files >= threshold
            and summary.descendant_agents == 0
        )
    ]
    if hotspots:
        print("  hotspot children without AGENTS:")
        for summary in hotspots:
            print(
                "    - "
                f"{format_path(summary.path, cwd)}/ -> "
                f"{summary.code_files} code files, {summary.nested_dirs} nested dirs"
            )
    else:
        print("  hotspot children without AGENTS: none")

    second_level_hotspots = []
    for summary in summaries:
        for nested in nested_hotspots(summary, agents, threshold):
            second_level_hotspots.append((summary.path, nested))

    if second_level_hotspots:
        print("  nested hotspot children under umbrella dirs:")
        for parent, nested in second_level_hotspots:
            print(
                "    - "
                f"{format_path(nested.path, cwd)}/ -> "
                f"{nested.code_files} code files, {nested.nested_dirs} nested dirs "
                f"(under {format_path(parent, cwd)}/)"
            )
    else:
        print("  nested hotspot children under umbrella dirs: none")

    print()


def main() -> int:
    args = parse_args()
    cwd = Path.cwd().resolve()
    roots = [Path(root).resolve() for root in args.roots]
    agents = discover_agents(roots)

    if not agents:
        print("No AGENTS.md files found.")
        return 0

    print("# AGENTS inventory")
    print(f"roots: {', '.join(format_path(root, cwd) for root in roots)}")
    print()

    scope_dirs = sorted(agent.parent for agent in agents)
    for scope_dir in scope_dirs:
        print_scope(scope_dir, agents, cwd, args.hotspot_threshold)

    print("Notes:")
    print("- This is a heuristic inventory, not an automatic decision engine.")
    print("- A hotspot is only a candidate for a deeper AGENTS.md.")
    print("- Always verify architecture claims against live code before reporting drift.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
