# NYT-like Crossword (5x5 Cross Lines) — Streamlit + Custom Component

## What this is
A ready-to-run Streamlit app that loads puzzles from `./puzzles/*.json` and provides NYT-style grid interaction:
- Mouse click to move cursor
- Keyboard typing A–Z
- Arrow keys navigation
- Tab / Shift+Tab to jump between entries
- Space (or clicking the same intersection) toggles orientation H/V at intersections
- "Check Word" (active entry) and "Check Puzzle"

Grid geometry is fixed (5×5), with playable cells defined by:
- playable rows (1-based): 2 and 4
- playable cols (1-based): 2 and 4
- playable = union of those rows/cols; all others are black

Entries:
- h1 (row 2), h2 (row 4), v1 (col 2), v2 (col 4), hw (4 intersections, clockwise)

## Run
From the unzipped folder:

```bash
python -m venv .venv
# activate venv
pip install -r requirements.txt
streamlit run app/app.py
```

## Add puzzles
Drop JSON files into `./puzzles/` following the schema in `septago_crossword/puzzle_schema.md`.
