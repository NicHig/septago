from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace
from typing import Dict, List, Literal, TypedDict

from .geometry import GridSpec, GridBarId
from .puzzle_io import Puzzle


# -----------------------------
# Contracts
# -----------------------------

BarScope = Literal["grid", "hidden"]
Direction = Literal["horizontal", "vertical"]


class BarRef(TypedDict):
    scope: BarScope
    bar_id: str
    length: int


class ActiveRef(TypedDict):
    scope: BarScope
    bar_id: str
    index: int
    direction: Direction


EventType = Literal[
    "INPUT_LETTER",
    "MOVE_NEXT",
    "MOVE_PREV",
    "SET_ACTIVE_BAR",
    "BACKSPACE",
    "RESET",
    "TICK",  # no-op; kept for backward compatibility
]


@dataclass(frozen=True)
class GridEvent:
    type: EventType
    payload: dict


@dataclass(frozen=True)
class GameState:
    puzzle_id: str
    state_id: str  # unique per init/reset (forces frontend resync)

    grid_cells: Dict[GridBarId, List[str]]
    hidden_cells: Dict[str, List[str]]

    active: ActiveRef
    clue_order: List[BarRef]

    start_time: float

    # Highest client_seq processed so far (frontend uses this to ignore stale renders)
    last_client_seq: int = 0
    last_action: str = ""


GRID_ORDER: List[GridBarId] = ["h1", "h2", "h3", "v1", "v2", "v3"]


def _bar_direction(bar_id: str) -> Direction:
    if bar_id in ("h1", "h2", "h3"):
        return "horizontal"
    if bar_id in ("v1", "v2", "v3"):
        return "vertical"
    return "horizontal"


def _first_empty_index(cells: List[str]) -> int:
    for i, ch in enumerate(cells):
        if (ch or "") == "":
            return i
    return 0


def derive_intersection_letters(state: GameState, grid: GridSpec) -> List[str]:
    out: List[str] = []
    for (bar_id, idx) in grid.intersections:
        val = state.grid_cells.get(bar_id, [""])[idx]
        out.append(val or "")
    return out


def is_complete(state: GameState) -> bool:
    all_grid = all(all((ch or "") != "" for ch in state.grid_cells[bid]) for bid in state.grid_cells)
    all_hidden = all(all((ch or "") != "" for ch in arr) for arr in state.hidden_cells.values())
    return bool(all_grid and all_hidden)


def init_state(puzzle: Puzzle, grid: GridSpec) -> GameState:
    # Initialize empty grid cells
    grid_cells: Dict[GridBarId, List[str]] = {bid: ["" for _ in range(grid.bar_lengths[bid])] for bid in GRID_ORDER}

    # Hidden bars are driven by puzzle.answers.hidden (1 or 2 strings)
    hidden_answers = puzzle.answers.get("hidden", [])
    if not isinstance(hidden_answers, list) or not hidden_answers:
        hidden_answers = [""]

    hidden_cells: Dict[str, List[str]] = {}
    for i, word in enumerate(hidden_answers, start=1):
        hid = f"hidden{i}"
        hidden_cells[hid] = ["" for _ in range(len(str(word)))]

    # Clue order: grid order then hidden bars
    clue_order: List[BarRef] = [
        {"scope": "grid", "bar_id": bid, "length": int(grid.bar_lengths[bid])}
        for bid in GRID_ORDER
    ]
    for hid, arr in hidden_cells.items():
        clue_order.append({"scope": "hidden", "bar_id": hid, "length": len(arr)})

    # Default active: first grid bar (h1) at first empty
    active: ActiveRef = {
        "scope": "grid",
        "bar_id": "h1",
        "index": 0,
        "direction": "horizontal",
    }

    puzzle_id = str((puzzle.meta or {}).get("id", puzzle.filename))
    return GameState(
        puzzle_id=puzzle_id,
        state_id=str(uuid.uuid4()),
        grid_cells=grid_cells,
        hidden_cells=hidden_cells,
        active=active,
        clue_order=clue_order,
        start_time=time.time(),
        last_client_seq=0,
        last_action="init",
    )


# -----------------------------
# Reducer
# -----------------------------


def reduce(state: GameState, event: GridEvent, grid: GridSpec) -> GameState:
    """Authoritative state transition (server-side)."""
    payload = event.payload or {}
    client_seq = payload.get("client_seq")
    try:
        client_seq_int = int(client_seq) if client_seq is not None else None
    except Exception:
        client_seq_int = None

    t = event.type
    if t == "INPUT_LETTER":
        out = _on_input_letter(state, payload, grid)
    elif t == "MOVE_NEXT":
        out = _on_move(state, step=1)
    elif t == "MOVE_PREV":
        out = _on_move(state, step=-1)
    elif t == "SET_ACTIVE_BAR":
        out = _on_set_active(state, payload)
    elif t == "BACKSPACE":
        out = _on_backspace(state, grid)
    elif t == "RESET":
        out = _on_reset(state)
    elif t == "TICK":
        out = state
    else:
        out = replace(state, last_action=f"ignored:{t}")

    if client_seq_int is not None and client_seq_int > out.last_client_seq:
        out = replace(out, last_client_seq=client_seq_int)
    return out


def _clamp(i: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, i))


def _on_set_active(state: GameState, payload: dict) -> GameState:
    scope = str(payload.get("scope", "grid"))
    bar_id = str(payload.get("bar_id", "h1"))
    idx_raw = payload.get("index", None)

    if scope not in ("grid", "hidden"):
        scope = "grid"

    if scope == "grid":
        if bar_id not in state.grid_cells:
            bar_id = "h1"
        cells = state.grid_cells[bar_id]  # type: ignore[index]
        direction = _bar_direction(bar_id)
    else:
        if bar_id not in state.hidden_cells:
            bar_id = next(iter(state.hidden_cells.keys()), "hidden1")
        cells = state.hidden_cells.get(bar_id, [])
        direction = "horizontal"

    if idx_raw is None:
        idx = _first_empty_index(cells)
    else:
        try:
            idx = int(idx_raw)
        except Exception:
            idx = 0
        idx = _clamp(idx, 0, max(0, len(cells) - 1))

    new_active: ActiveRef = {
        "scope": scope,  # type: ignore[assignment]
        "bar_id": bar_id,
        "index": idx,
        "direction": direction,
    }

    return replace(state, active=new_active, last_action="set_active")


def _on_move(state: GameState, step: int) -> GameState:
    a = state.active
    scope = a["scope"]
    bar_id = a["bar_id"]
    idx = int(a["index"])

    if scope == "grid":
        cells = state.grid_cells.get(bar_id, [])  # type: ignore[arg-type]
    else:
        cells = state.hidden_cells.get(bar_id, [])

    if not cells:
        return replace(state, last_action="move:empty")

    nxt = _clamp(idx + step, 0, len(cells) - 1)
    if nxt == idx:
        return replace(state, last_action="move:edge")

    new_active = dict(a)
    new_active["index"] = nxt
    return replace(state, active=new_active, last_action="move")


def _on_input_letter(state: GameState, payload: dict, grid: GridSpec) -> GameState:
    letter = str(payload.get("letter", "")).upper().strip()
    if len(letter) != 1 or not ("A" <= letter <= "Z"):
        return replace(state, last_action="input:ignored")

    a = state.active
    scope = a["scope"]
    bar_id = a["bar_id"]
    idx = int(a["index"])

    if scope == "hidden":
        if bar_id not in state.hidden_cells:
            return replace(state, last_action="input:hidden_missing")
        arr = list(state.hidden_cells[bar_id])
        if idx < 0 or idx >= len(arr):
            return replace(state, last_action="input:oob")
        arr[idx] = letter
        new_hidden = dict(state.hidden_cells)
        new_hidden[bar_id] = arr
        out = replace(state, hidden_cells=new_hidden, last_action="input:hidden")
        return _on_move(out, step=1)

    # Grid
    if bar_id not in state.grid_cells:
        return replace(state, last_action="input:grid_missing")
    arr = list(state.grid_cells[bar_id])  # type: ignore[arg-type]
    if idx < 0 or idx >= len(arr):
        return replace(state, last_action="input:oob")
    arr[idx] = letter
    new_grid = dict(state.grid_cells)
    new_grid[bar_id] = arr  # type: ignore[index]

    # Cross-link at intersections
    key = (bar_id, idx)  # type: ignore[arg-type]
    if key in grid.cross_map:
        other_bar, other_idx = grid.cross_map[key]
        other_arr = list(new_grid.get(other_bar, [""] * grid.bar_lengths[other_bar]))
        if 0 <= other_idx < len(other_arr):
            other_arr[other_idx] = letter
            new_grid[other_bar] = other_arr

    out = replace(state, grid_cells=new_grid, last_action="input:grid")
    return _on_move(out, step=1)


def _on_backspace(state: GameState, grid: GridSpec) -> GameState:
    """
    Backspace behavior:
    - If current cell has a letter: clear it.
    - Else (already empty): move back one cell and clear that.
    - For grid intersections: clear partner cell too (same behavior).
    """
    a = state.active
    scope = a["scope"]
    bar_id = a["bar_id"]
    idx = int(a["index"])

    if scope == "hidden":
        if bar_id not in state.hidden_cells:
            return replace(state, last_action="bksp:hidden_missing")
        arr = list(state.hidden_cells[bar_id])
        if not arr:
            return replace(state, last_action="bksp:hidden_empty")

        idx = _clamp(idx, 0, len(arr) - 1)

        if (arr[idx] or "") != "":
            arr[idx] = ""
            new_hidden = dict(state.hidden_cells)
            new_hidden[bar_id] = arr
            return replace(state, hidden_cells=new_hidden, last_action="bksp:hidden_clear")

        if idx > 0:
            idx2 = idx - 1
            arr[idx2] = ""
            new_hidden = dict(state.hidden_cells)
            new_hidden[bar_id] = arr
            new_active = dict(a)
            new_active["index"] = idx2
            return replace(state, hidden_cells=new_hidden, active=new_active, last_action="bksp:hidden_prev_clear")

        return replace(state, last_action="bksp:hidden_edge")

    # grid
    if bar_id not in state.grid_cells:
        return replace(state, last_action="bksp:grid_missing")
    arr = list(state.grid_cells[bar_id])  # type: ignore[arg-type]
    if not arr:
        return replace(state, last_action="bksp:grid_empty")

    idx = _clamp(idx, 0, len(arr) - 1)

    def clear_at(b: GridBarId, i: int, grid_cells: Dict[GridBarId, List[str]]) -> Dict[GridBarId, List[str]]:
        out = dict(grid_cells)
        a2 = list(out.get(b, [""] * grid.bar_lengths[b]))
        if 0 <= i < len(a2):
            a2[i] = ""
            out[b] = a2
        return out

    def clear_with_partner(b: GridBarId, i: int, grid_cells: Dict[GridBarId, List[str]]) -> Dict[GridBarId, List[str]]:
        out = clear_at(b, i, grid_cells)
        key = (b, i)
        if key in grid.cross_map:
            ob, oi = grid.cross_map[key]
            out = clear_at(ob, oi, out)
        return out

    # if current has a letter, clear it
    if (arr[idx] or "") != "":
        new_grid = clear_with_partner(bar_id, idx, state.grid_cells)  # type: ignore[arg-type]
        return replace(state, grid_cells=new_grid, last_action="bksp:grid_clear")

    # else move back one and clear there
    if idx > 0:
        idx2 = idx - 1
        new_grid = clear_with_partner(bar_id, idx2, state.grid_cells)  # type: ignore[arg-type]
        new_active = dict(a)
        new_active["index"] = idx2
        return replace(state, grid_cells=new_grid, active=new_active, last_action="bksp:grid_prev_clear")

    return replace(state, last_action="bksp:grid_edge")


def _on_reset(state: GameState) -> GameState:
    # Clear all cells, reset timer, keep puzzle_id.
    new_grid = {bid: ["" for _ in arr] for bid, arr in state.grid_cells.items()}
    new_hidden = {hid: ["" for _ in arr] for hid, arr in state.hidden_cells.items()}

    # Reset active to first bar
    new_active: ActiveRef = {
        "scope": "grid",
        "bar_id": "h1",
        "index": 0,
        "direction": "horizontal",
    }

    return replace(
        state,
        state_id=str(uuid.uuid4()),
        grid_cells=new_grid,  # type: ignore[arg-type]
        hidden_cells=new_hidden,
        active=new_active,
        start_time=time.time(),
        last_client_seq=0,
        last_action="reset",
    )