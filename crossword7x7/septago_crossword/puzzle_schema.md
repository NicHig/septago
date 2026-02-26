# PuzzleFile v2 Schema (normative)

This app expects `schema_version: "puzzlefile.v2"`.

```json
{
  "schema_version": "puzzlefile.v2",
  "meta": {
    "id": "0001",
    "title": "My Puzzle",
    "subtitle": "Optional",
    "author": "You",
    "date": "YYYY-MM-DD",
    "difficulty": "Optional",
    "instructions": "Optional markdown shown in the app"
  },
  "answers": {
    "h1": "AAAAAAA",
    "h2": "BBBBBBB",
    "h3": "CCCCCCC",
    "v1": "DDDDDDD",
    "v2": "EEEEEEE",
    "v3": "FFFFFFF",
    "hidden": ["HIDDENWORD"]
  },
  "clues": {
    "h1": "string",
    "h2": "string",
    "h3": "string",
    "v1": "string",
    "v2": "string",
    "v3": "string",
    "hidden": ["Hidden clue"]
  }
}
```

## Hidden rules

- `answers.hidden` must be an array of **1 or 2** strings.
  - 1 element → one hidden bar (length = len(string))
  - 2 elements → two hidden bars
- `clues.hidden` must be an array with the **same length** as `answers.hidden`.
- Hidden bars are validated **independently** from the intersection pool.
