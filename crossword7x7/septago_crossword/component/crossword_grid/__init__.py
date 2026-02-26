from __future__ import annotations

from pathlib import Path
import streamlit.components.v1 as components

_FRONTEND_DIR = (Path(__file__).parent / "frontend").resolve()
_INDEX = _FRONTEND_DIR / "index.html"

if not _INDEX.exists():
    raise RuntimeError(
        f"Crossword component frontend not found. Expected: {_INDEX}\n"
        f"Directory contents: {list(_FRONTEND_DIR.glob('*'))}"
    )

_crossword_grid = components.declare_component(
    name="crossword_grid",
    # as_posix() avoids some Windows/backslash edge cases in Streamlit
    path=_FRONTEND_DIR.as_posix(),
)

def crossword_grid(props: dict, key: str = "crossword_grid"):
    return _crossword_grid(props=props, key=key, default=None)