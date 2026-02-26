from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import streamlit as st

from septago_crossword.geometry import build_grid_spec
from septago_crossword.puzzle_io import list_puzzles, load_puzzle, PuzzleValidationError
from septago_crossword.engine import init_state, reduce, GridEvent, GameState
from septago_crossword.ui_adapters import make_component_props
from septago_crossword.component.crossword_grid import crossword_grid


APP_TITLE = "Septago Crossword"

DEFAULT_INSTRUCTIONS = """**How to Play**

Each clue contains two possible words which are anagrams of each other.
Each anagram takes a meaning from the clue.

Fill the **7×7 grid** and then fill the **hidden bar(s)**.
The **intersection pool** shows the current letters at grid intersections (display-only).

- Click a square to focus it.
- Type to fill.
- Arrow keys move within the active bar.
- At grid intersections, arrowing across the other direction toggles the active bar.
- **Tab / Enter** cycles through clues. **Shift+Tab** goes back.
- **Space** advances one cell (never inserts a space).
- **Backspace** deletes letters.
"""


def _ensure_state() -> None:
    if "grid_spec" not in st.session_state:
        st.session_state.grid_spec = build_grid_spec()

    if "puzzle" not in st.session_state:
        st.session_state.puzzle = None
    if "game_state" not in st.session_state:
        st.session_state.game_state = None
    if "last_event_id" not in st.session_state:
        st.session_state.last_event_id = None
    if "show_instructions" not in st.session_state:
        st.session_state.show_instructions = False

    # Per-cell correctness marks (set by Check Word / Check Puzzle)
    if "marks" not in st.session_state:
        st.session_state.marks = {"grid": {}, "hidden": {}}


def _get_bar_string(state: GameState, scope: str, bar_id: str) -> str:
    if scope == "hidden":
        arr = state.hidden_cells.get(bar_id, [])
    else:
        arr = state.grid_cells.get(bar_id, [])  # type: ignore[arg-type]
    return "".join([ch or "" for ch in arr])


def _get_hidden_answer(puzzle, bar_id: str) -> str:
    hidden = puzzle.answers.get("hidden", [])
    if not isinstance(hidden, list):
        return ""
    # bar_id like hidden1/hidden2
    try:
        idx = int(bar_id.replace("hidden", "")) - 1
    except Exception:
        idx = 0
    if 0 <= idx < len(hidden):
        return str(hidden[idx] or "")
    return ""


def _check_word(puzzle, state: GameState) -> None:
    # Mark correctness for the *active* bar only (no banners).
    a = state.active
    scope = a["scope"]
    bar_id = a["bar_id"]

    if scope == "hidden":
        answer = _get_hidden_answer(puzzle, bar_id)
        if not answer:
            return
        arr = list(state.hidden_cells.get(bar_id, []))
        out = []
        for i, ch in enumerate(arr):
            expected = answer[i].upper() if i < len(answer) else ""
            got = (ch or "").upper()
            out.append("correct" if got and got == expected else "wrong")
        st.session_state.marks = {"grid": {}, "hidden": {bar_id: out}}
        return

    answer = str(puzzle.answers.get(bar_id, "") or "")
    if not answer:
        return

    grid_spec = st.session_state.grid_spec
    cells = grid_spec.bars.get(bar_id, [])
    marks_grid = {}
    for i, cell in enumerate(cells):
        cid = f"{cell[0]},{cell[1]}"
        got = (state.grid_cells.get(bar_id, [""])[i] or "").upper()
        expected = answer[i].upper() if i < len(answer) else ""
        marks_grid[cid] = "correct" if got and got == expected else "wrong"
    st.session_state.marks = {"grid": marks_grid, "hidden": {}}


def _check_puzzle(puzzle, state: GameState) -> None:
    # Mark correctness for *all* playable squares (grid + hidden).
    grid_spec = st.session_state.grid_spec

    marks_grid = {}
    for bid in ["h1", "h2", "h3", "v1", "v2", "v3"]:
        ans = str(puzzle.answers.get(bid, "") or "")
        if not ans:
            continue
        cells = grid_spec.bars.get(bid, [])
        for i, cell in enumerate(cells):
            cid = f"{cell[0]},{cell[1]}"
            got = (state.grid_cells.get(bid, [""])[i] or "").upper()
            expected = ans[i].upper() if i < len(ans) else ""
            marks_grid[cid] = "correct" if got and got == expected else "wrong"

    marks_hidden = {}
    for hid, arr in state.hidden_cells.items():
        ans = _get_hidden_answer(puzzle, hid)
        if not ans:
            continue
        out = []
        for i, ch in enumerate(arr):
            expected = ans[i].upper() if i < len(ans) else ""
            got = (ch or "").upper()
            out.append("correct" if got and got == expected else "wrong")
        marks_hidden[hid] = out

    st.session_state.marks = {"grid": marks_grid, "hidden": marks_hidden}


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🧩", layout="wide")
    _ensure_state()

    grid_spec = st.session_state.grid_spec
    puzzle_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "puzzles")
    metas = list_puzzles(puzzle_dir)

    with st.sidebar:
        st.header("Puzzle")
        if not metas:
            st.warning(f"No puzzles found in {puzzle_dir}")
            st.stop()

        options = {f"{m.id} — {m.title}": m for m in metas}
        pick = st.selectbox("Select a puzzle", list(options.keys()))
        chosen = options[pick]

        load_clicked = st.button("Load puzzle", type="primary", use_container_width=True)
        reset_clicked = st.button(
            "Reset", use_container_width=True, disabled=st.session_state.game_state is None
        )

        # Restore checks (MVP)
        check_word_clicked = st.button(
            "Check Word", use_container_width=True, disabled=st.session_state.game_state is None
        )
        check_puzzle_clicked = st.button(
            "Check Puzzle", use_container_width=True, disabled=st.session_state.game_state is None
        )

        st.divider()
        if st.button("Instructions", use_container_width=True):
            st.session_state.show_instructions = not st.session_state.show_instructions

    if load_clicked:
        path = os.path.join(puzzle_dir, chosen.filename)
        try:
            puzzle = load_puzzle(path, grid_spec)
        except PuzzleValidationError as e:
            st.session_state.puzzle = None
            st.session_state.game_state = None
            st.error(f"Puzzle invalid: {e}")
        else:
            st.session_state.puzzle = puzzle
            st.session_state.game_state = init_state(puzzle, grid_spec)
            st.session_state.last_event_id = None
            st.session_state.marks = {"grid": {}, "hidden": {}}

    if reset_clicked and st.session_state.game_state is not None:
        st.session_state.game_state = reduce(
            st.session_state.game_state, GridEvent(type="RESET", payload={}), grid_spec
        )
        st.session_state.last_event_id = None
        st.session_state.marks = {"grid": {}, "hidden": {}}

    puzzle = st.session_state.puzzle
    game_state = st.session_state.game_state

    # Header from puzzle meta if available
    if puzzle is not None:
        title = str((puzzle.meta or {}).get("title", chosen.title)).strip() or chosen.title
        subtitle = str((puzzle.meta or {}).get("subtitle", chosen.subtitle)).strip() or chosen.subtitle
        instructions = (puzzle.meta or {}).get("instructions", DEFAULT_INSTRUCTIONS)
    else:
        title = str(chosen.title).strip() or APP_TITLE
        subtitle = str(chosen.subtitle).strip()
        instructions = DEFAULT_INSTRUCTIONS

    st.title(title)
    if subtitle:
        st.caption(subtitle)

    if st.session_state.show_instructions:
        with st.expander("Instructions", expanded=True):
            st.markdown(instructions)

    if puzzle is None or game_state is None:
        st.info("Load a puzzle to start playing.")
        st.stop()

    # Render the single unified component (includes its own clue list + reset button).
    props = make_component_props(game_state, grid_spec, puzzle, marks=st.session_state.marks)
    event = crossword_grid(props, key="crossword")

    # Process component events (dedupe by event_id)
    if isinstance(event, dict) and event.get("schema_version") == "crossword.v2":
        ev_id = event.get("event_id")
        if ev_id and ev_id != st.session_state.last_event_id:
            st.session_state.last_event_id = ev_id
            etype = event.get("type", "")
            payload = event.get("payload", {}) or {}

            # Ignore stale events from previous state_id
            ev_state_id = payload.get("state_id")
            cur_state_id = getattr(st.session_state.game_state, "state_id", None)
            if ev_state_id is not None and cur_state_id is not None and ev_state_id != cur_state_id:
                pass
            else:
                st.session_state.game_state = reduce(
                    st.session_state.game_state,
                    GridEvent(type=etype, payload=payload),
                    grid_spec,
                )

                # Any edit invalidates prior check markings.
                if etype in ("INPUT_LETTER", "BACKSPACE", "RESET"):
                    st.session_state.marks = {"grid": {}, "hidden": {}}
    # Apply check actions AFTER processing any pending grid events to avoid clobbering recent keystrokes.
    if check_word_clicked:
        _check_word(puzzle, st.session_state.game_state)
        st.rerun()
    if check_puzzle_clicked:
        _check_puzzle(puzzle, st.session_state.game_state)
        st.rerun()



if __name__ == "__main__":
    main()
