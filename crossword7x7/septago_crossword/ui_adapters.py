from __future__ import annotations

import time
from typing import Any, Dict, List

from .engine import GameState, is_complete
from .geometry import GridSpec, Cell
from .puzzle_io import Puzzle


def cell_id(cell: Cell) -> str:
    return f"{cell[0]},{cell[1]}"


def _cell_letter_from_state(state: GameState, grid: GridSpec, cell: Cell) -> str:
    pos = grid.cell_to_positions.get(cell, [])
    if not pos:
        return ""
    bar_id, idx = pos[0]
    return state.grid_cells.get(bar_id, [""])[idx] or ""


def make_component_props(
    state: GameState,
    grid: GridSpec,
    puzzle: Puzzle,
    marks: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build props for the unified crossword.v2 component."""

    # Grid cell payload (still cell-based for rendering simplicity)
    cells_payload: List[Dict[str, Any]] = []
    for r in range(grid.size):
        for c in range(grid.size):
            playable = grid.playable_mask[r][c]
            cell = (r, c)
            is_black = not playable
            letter = _cell_letter_from_state(state, grid, cell) if playable else ""

            # Active highlight only when scope == grid
            active_cell = False
            active_slot = False
            if playable and state.active["scope"] == "grid":
                ab = state.active["bar_id"]
                ai = int(state.active["index"])
                if ab in grid.bars and 0 <= ai < len(grid.bars[ab]):
                    active_cell = (grid.bars[ab][ai] == cell)
                active_slot = cell in set(grid.bars.get(ab, []))

            cells_payload.append(
                {
                    "id": cell_id(cell),
                    "r": r,
                    "c": c,
                    "is_black": is_black,
                    "is_playable": playable,
                    "letter": letter,
                    "highlight": {
                        "active_cell": active_cell,
                        "active_slot": active_slot,
                    },
                }
            )

    # Bar geometry for JS local-first navigation
    bars_payload: Dict[str, List[str]] = {bid: [cell_id(c) for c in cells] for bid, cells in grid.bars.items()}

    # cross_map: encode keys as "bar_id:index" strings for JSON
    cross_payload: Dict[str, str] = {
        f"{a[0]}:{a[1]}": f"{b[0]}:{b[1]}" for a, b in grid.cross_map.items()
    }

    # Intersection pool now sends ordered CELL IDs (JS derives letters from local gridLetters)
    intersection_cells: List[str] = []
    for (bar_id, idx) in grid.intersections:
        cell = grid.bars[bar_id][idx]
        intersection_cells.append(cell_id(cell))

    # Hidden bars payload
    hidden_payload: Dict[str, Any] = {}
    for hid, arr in state.hidden_cells.items():
        hidden_payload[hid] = {
            "id": hid,
            "length": len(arr),
            "letters": list(arr),
        }

    # Clues: pass to JS for local-first clicking.
    clues: Dict[str, Any] = dict(puzzle.clues)
    hidden_clues = clues.get("hidden", [])
    if isinstance(hidden_clues, list):
        for i, hid in enumerate(sorted(state.hidden_cells.keys())):
            clues[hid] = hidden_clues[i] if i < len(hidden_clues) else ""

    complete = is_complete(state)

    # Timer
    now = time.time()
    elapsed = int(max(0.0, now - float(state.start_time)))
    start_time_epoch = int(float(state.start_time))

    return {
        "schema_version": "crossword.v2.props",
        "grid": {
            "size": grid.size,
            "cells": cells_payload,
            "bars": bars_payload,
            "cross_map": cross_payload,
            "bar_order": ["h1", "h2", "h3", "v1", "v2", "v3"],
        },
        "hidden": {
            "bars": hidden_payload,
            "bar_order": sorted(state.hidden_cells.keys()),
        },
        "intersection_pool": {
            "cells": intersection_cells,
        },
        "clues": clues,
        "focus": {
            "active": dict(state.active),
        },
        "status": {
            "complete": complete,
            "elapsed_seconds": elapsed,
            "start_time_epoch": start_time_epoch,
        },
        "sync": {
            "last_client_seq": int(getattr(state, "last_client_seq", 0)),
            "puzzle_id": getattr(state, "puzzle_id", ""),
            "state_id": getattr(state, "state_id", ""),
        },
        "marks": marks or {"grid": {}, "hidden": {}},
    }