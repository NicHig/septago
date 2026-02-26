from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, TypedDict

from .geometry import GridSpec, GridBarId


class PuzzleValidationError(ValueError):
    pass


def _norm_letters(s: str) -> str:
    return "".join(ch for ch in str(s).upper().strip() if ch != " ")


def _validate_letters_only(s: str) -> None:
    for ch in s:
        if "A" <= ch <= "Z":
            continue
        raise PuzzleValidationError(
            f"Invalid character '{ch}' in string '{s}'. Only A–Z allowed."
        )


@dataclass(frozen=True)
class PuzzleMeta:
    id: str
    title: str
    subtitle: str
    author: str
    date: str
    difficulty: str
    filename: str


class PuzzleAnswers(TypedDict, total=False):
    h1: str
    h2: str
    h3: str
    v1: str
    v2: str
    v3: str
    hidden: List[str]


class PuzzleClues(TypedDict, total=False):
    h1: str
    h2: str
    h3: str
    v1: str
    v2: str
    v3: str
    hidden: List[str]


@dataclass(frozen=True)
class Puzzle:
    schema_version: str
    meta: dict
    answers: Dict[str, object]
    clues: Dict[str, object]
    filename: str


def list_puzzles(puzzle_dir: str) -> List[PuzzleMeta]:
    metas: List[PuzzleMeta] = []
    if not os.path.isdir(puzzle_dir):
        return metas

    for fn in sorted(os.listdir(puzzle_dir)):
        if not fn.lower().endswith(".json"):
            continue
        path = os.path.join(puzzle_dir, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            meta = raw.get("meta", {}) or {}
            metas.append(
                PuzzleMeta(
                    id=str(meta.get("id", fn.replace(".json", ""))),
                    title=str(meta.get("title", fn.replace(".json", ""))),
                    subtitle=str(meta.get("subtitle", "")),
                    author=str(meta.get("author", "")),
                    date=str(meta.get("date", "")),
                    difficulty=str(meta.get("difficulty", "")),
                    filename=fn,
                )
            )
        except Exception:
            continue
    return metas


def _get_required_grid_len(grid: GridSpec, bar_id: GridBarId) -> int:
    return int(grid.bar_lengths[bar_id])


def load_puzzle(path: str, grid: GridSpec) -> Puzzle:
    """Load upgraded puzzle schema.

    Schema
    ------
    {
      "schema_version": "puzzlefile.v2",
      "answers": {"h1": ".......", ..., "hidden": ["WORD"] or ["FOUR","FIVE"]},
      "clues":   {"h1": "...", ..., "hidden": ["Clue 1", "Clue 2?"]},
      "meta":    {...}
    }
    """

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    schema_version = str(raw.get("schema_version", "")).strip()
    if schema_version != "puzzlefile.v2":
        raise PuzzleValidationError(
            f"Unsupported or missing schema_version: {schema_version!r}. Expected 'puzzlefile.v2'."
        )

    meta = raw.get("meta", {}) or {}
    answers_raw = raw.get("answers", {}) or {}
    clues_raw = raw.get("clues", {}) or {}

    required_grid: List[GridBarId] = ["h1", "h2", "h3", "v1", "v2", "v3"]
    missing = [bid for bid in required_grid if bid not in answers_raw]
    if missing:
        raise PuzzleValidationError(f"Puzzle missing required grid answers: {missing}")

    # Validate grid answers
    for bid in required_grid:
        ans = _norm_letters(answers_raw.get(bid, ""))
        if not ans:
            raise PuzzleValidationError(f"answers.{bid} missing non-empty string")
        _validate_letters_only(ans)
        required_len = _get_required_grid_len(grid, bid)
        if len(ans) != required_len:
            raise PuzzleValidationError(
                f"answers.{bid} length {len(ans)} != required {required_len}"
            )

    # Validate hidden bars
    hidden = answers_raw.get("hidden", None)
    if not isinstance(hidden, list) or not hidden:
        raise PuzzleValidationError("answers.hidden must be a non-empty array of 1 or 2 strings")
    if len(hidden) not in (1, 2):
        raise PuzzleValidationError("answers.hidden must have length 1 or 2")

    hidden_norm: List[str] = []
    for i, hw in enumerate(hidden):
        hw_s = _norm_letters(hw)
        if not hw_s:
            raise PuzzleValidationError(f"answers.hidden[{i}] missing non-empty string")
        _validate_letters_only(hw_s)
        hidden_norm.append(hw_s)

    # Validate clues (non-empty for grid; hidden clues length matches hidden bars, but can be blank strings)
    for bid in required_grid:
        clue = str(clues_raw.get(bid, "")).strip()
        if not clue:
            raise PuzzleValidationError(f"clues.{bid} missing non-empty string")

    hidden_clues = clues_raw.get("hidden", None)
    if not isinstance(hidden_clues, list) or len(hidden_clues) != len(hidden_norm):
        raise PuzzleValidationError(
            "clues.hidden must be an array with the same length as answers.hidden"
        )

    # Build canonical dicts
    answers: Dict[str, object] = {bid: _norm_letters(answers_raw[bid]) for bid in required_grid}
    answers["hidden"] = hidden_norm

    clues: Dict[str, object] = {bid: str(clues_raw.get(bid, "")).strip() for bid in required_grid}
    clues["hidden"] = [str(x).strip() for x in hidden_clues]

    return Puzzle(
        schema_version=schema_version,
        meta=meta,
        answers=answers,
        clues=clues,
        filename=os.path.basename(path),
    )
