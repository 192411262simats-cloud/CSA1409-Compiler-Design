# Compiler-Driven SQL Query Performance Enhancement System

Local FastAPI and SQLite application for SQL compiler analysis, safe rewrites, demo query execution, timing comparison, history, and exports.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn backend.app.main:app --reload
```

Open http://127.0.0.1:8000. SQLite demo and analysis history files are created under `data/` on first run. The demo starts with 400 students and 1,600 enrollments.

## Scope and measurement

The API parses with SQLGlot, validates tables and columns against the demo schema, emits token positions and an AST-based intermediate representation, and removes duplicate predicates from AND conditions. It executes original and rewritten SELECT statements in the same local SQLite database and reports averages from 1–10 runs. SQLite plan output is qualitative; the app does not invent a numeric cost. Results are capped at 500 rows, and execution is read-only with a two-second query deadline.

PDF export uses the browser's Print / Save as PDF flow. CSV exports saved history. The database files are ignored by Git and recreated automatically.
