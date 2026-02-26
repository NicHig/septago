from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple, Literal

# --- Types ---

Cell = Tuple[int, int]

# Grid bars (7×7 Septago): 3 horizontals, 3 verticals.
GridBarId = Literal["h1", "h2", "h3", "v1", "v2", "v3"]


@dataclass(frozen=True)
class GridSpec:
    """Static geometry contract.

    Notes
    -----
    - Hidden bars are NOT part of this geometry.
    - Intersections are defined for display (intersection pool) and for
      cross-linking (cross_map) between grid bars.
    """

    size: int
    playable_mask: List[List[bool]]

    # Bar -> ordered list of cells
    bars: Dict[GridBarId, List[Cell]]

    # Cell -> list of (bar_id, index) positions (1 for non-intersections, 2 for intersections)
    cell_to_positions: Dict[Cell, List[Tuple[GridBarId, int]]]

    # Cross-link between bar positions at intersections.
    # Key/value are (bar_id, index) pairs.
    cross_map: Dict[Tuple[GridBarId, int], Tuple[GridBarId, int]]

    # Canonical intersection order for display-only pool
    intersections: List[Tuple[GridBarId, int]]

    bar_lengths: Dict[GridBarId, int]


def build_grid_spec() -> GridSpec:
    """Build fixed 7×7 Septago geometry.

    Playable cells are the union of rows 2/4/6 and cols 2/4/6 (1-based).
    Intersections are the 3×3 crossings at (r,c) in {(1,3,5)}×{(1,3,5)} (0-based).
    """

    size = 7
    playable_rows = {1, 3, 5}
    playable_cols = {1, 3, 5}

    playable_mask: List[List[bool]] = []
    for r in range(size):
        row: List[bool] = []
        for c in range(size):
            row.append((r in playable_rows) or (c in playable_cols))
        playable_mask.append(row)

    bars: Dict[GridBarId, List[Cell]] = {
        "h1": [(1, c) for c in range(size)],
        "h2": [(3, c) for c in range(size)],
        "h3": [(5, c) for c in range(size)],
        "v1": [(r, 1) for r in range(size)],
        "v2": [(r, 3) for r in range(size)],
        "v3": [(r, 5) for r in range(size)],
    }

    # Build cell -> positions
    cell_to_positions: Dict[Cell, List[Tuple[GridBarId, int]]] = {}
    for bid, cells in bars.items():
        for i, cell in enumerate(cells):
            cell_to_positions.setdefault(cell, []).append((bid, i))

    # Cross map for intersections (every cell with 2 positions)
    cross_map: Dict[Tuple[GridBarId, int], Tuple[GridBarId, int]] = {}
    for cell, pos_list in cell_to_positions.items():
        if len(pos_list) != 2:
            continue
        a, b = pos_list
        cross_map[a] = b
        cross_map[b] = a

    # Canonical intersection order for the pool: row-major across the 3 intersection rows.
    # We choose the horizontal bar as canonical, so pool indexes reference h1/h2/h3.
    intersections: List[Tuple[GridBarId, int]] = []
    for r, h in [(1, "h1"), (3, "h2"), (5, "h3")]:
        for c in (1, 3, 5):
            intersections.append((h, c))

    bar_lengths = {bid: len(cells) for bid, cells in bars.items()}

    return GridSpec(
        size=size,
        playable_mask=playable_mask,
        bars=bars,
        cell_to_positions=cell_to_positions,
        cross_map=cross_map,
        intersections=intersections,
        bar_lengths=bar_lengths,
    )


def is_playable(grid: GridSpec, cell: Cell) -> bool:
    r, c = cell
    if r < 0 or c < 0 or r >= grid.size or c >= grid.size:
        return False
    return grid.playable_mask[r][c]


def first_playable_cell(grid: GridSpec) -> Cell:
    for r in range(grid.size):
        for c in range(grid.size):
            if grid.playable_mask[r][c]:
                return (r, c)
    raise RuntimeError("No playable cells in grid")
