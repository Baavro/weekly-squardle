from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import threading
import time
import uuid
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

log = logging.getLogger("squardle")

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DATABASE_PATH", ROOT / "squardle.sqlite3"))
WORDS_PATH = Path(os.getenv("WORDS_PATH", ROOT / "words.txt"))
ANSWERS_PATH = Path(os.getenv("ANSWERS_PATH", ROOT / "answers.txt"))
START_GUESSES, BONUS_LIMIT = 10, 8
GAME_TTL_DAYS = int(os.getenv("GAME_TTL_DAYS", "45"))
CREATE_LIMIT_PER_MIN = int(os.getenv("CREATE_LIMIT_PER_MIN", "20"))
# QA board: POST /api/test-game (used by /?test) always deals the fixed TEST_BOARD below.
# On by default so it works on Render too; set SQUARDLE_TEST_MODE=0 to switch it off.
TEST_MODE = os.getenv("SQUARDLE_TEST_MODE", "1").lower() not in ("0", "false", "no", "off")
TEST_BOARD = ["plugs", "linen", "amino", "nitro", "steep"]  # columns: plans limit unite genre snoop


def _read_words(path: Path) -> set[str]:
    with path.open(encoding="utf-8") as f:
        return {line.strip().lower() for line in f if len(line.strip()) == 5 and line.strip().isalpha()}


# Accepted guesses: every real five-letter word we know. Puzzle answers: a
# curated, common-word subset (no names, brands or vulgar words). If no answer
# list is bundled, fall back to the full guess list.
GUESS_WORDS = _read_words(WORDS_PATH)
ANSWER_WORDS = _read_words(ANSWERS_PATH) if ANSWERS_PATH.exists() else set()
if not ANSWER_WORDS:
    ANSWER_WORDS = set(GUESS_WORDS)
VALID_WORDS = GUESS_WORDS | ANSWER_WORDS
if not VALID_WORDS:
    raise RuntimeError("The five-letter word list is empty.")

# Indexes for fast grid generation. NEXT maps a column prefix (length 0-4) to
# the letters that can extend it to an answer word; BY_POS[i][ch] lists answer
# words having `ch` at position i.
NEXT: dict[str, set[str]] = defaultdict(set)
BY_POS: list[dict[str, list[str]]] = [defaultdict(list) for _ in range(5)]
for _w in ANSWER_WORDS:
    for _n in range(5):
        NEXT[_w[:_n]].add(_w[_n])
    for _i, _ch in enumerate(_w):
        BY_POS[_i][_ch].append(_w)

app = FastAPI(title="Squardle API", version="3.0.0", docs_url=None, redoc_url=None, openapi_url=None)
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "").split(",") if x.strip()]
if origins:
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
app.add_middleware(GZipMiddleware, minimum_size=800)

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", CSP)
    path = request.url.path
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif path == "/" or path.endswith((".html", ".js", ".css", ".webmanifest")):
        # Revalidate on every load (cheap 304 via ETag) so a deploy reaches players
        # immediately instead of leaving them on stale code for up to an hour.
        response.headers["Cache-Control"] = "no-cache"
    else:
        response.headers.setdefault("Cache-Control", "public, max-age=3600")
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "Something went wrong on our side. Please try again."}, status_code=500)


@contextmanager
def db():
    """Short-lived connection in autocommit mode; callers manage transactions explicitly."""
    con = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA synchronous=NORMAL")  # per connection; safe with WAL, saves an fsync per commit
    try:
        yield con
    finally:
        con.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("""CREATE TABLE IF NOT EXISTS games (
            id TEXT PRIMARY KEY, board_id TEXT NOT NULL, state_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_games_updated ON games(updated_at)")
        con.execute("""CREATE TABLE IF NOT EXISTS generated_boards (
            id TEXT PRIMARY KEY, rows_json TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_boards_rows ON generated_boards(rows_json)")


def purge_old_games():
    cutoff = (datetime.now(timezone.utc) - timedelta(days=GAME_TTL_DAYS)).isoformat()
    with db() as con:
        con.execute("DELETE FROM games WHERE updated_at < ?", (cutoff,))
        con.execute("DELETE FROM generated_boards WHERE created_at < ? AND id NOT IN (SELECT board_id FROM games)", (cutoff,))


init_db()
try:
    purge_old_games()
except sqlite3.Error:  # never block startup on housekeeping
    log.exception("Could not purge old games")


def validate_board(rows: list[str]) -> tuple[list[str], list[str]]:
    if len(rows) != 5 or any(len(w) != 5 or w.lower() not in VALID_WORDS for w in rows):
        raise ValueError("Every row must be a valid five-letter word in the bundled dictionary.")
    cols = ["".join(row[c] for row in rows) for c in range(5)]
    if any(w.lower() not in VALID_WORDS for w in cols):
        raise ValueError(f"Generated an invalid column word: {cols}")
    if len(set(w.lower() for w in rows + cols)) != 10:
        raise ValueError("A valid puzzle must contain ten distinct row and column words.")
    return rows, cols


def generate_board(max_nodes: int = 3000, max_attempts: int = 400) -> list[str]:
    """Generate a fresh valid word grid with randomized, prefix-pruned search.

    All ten words come from the curated answer vocabulary. Each attempt is
    node-limited so an unlucky start is abandoned quickly and retried.
    """
    for _ in range(max_attempts):
        rows: list[str] = []
        nodes = 0

        def search() -> list[str] | None:
            nonlocal nodes
            nodes += 1
            if nodes > max_nodes:
                return None
            depth = len(rows)
            if depth == 5:
                cols = ["".join(r[c] for r in rows) for c in range(5)]
                return rows.copy() if len(set(rows + cols)) == 10 else None
            allowed = [NEXT.get("".join(r[c] for r in rows)) for c in range(5)]
            if not all(allowed):
                return None
            # Iterate the most constrained column's letters, then filter the rest.
            pivot = min(range(5), key=lambda c: len(allowed[c]))
            candidates = [w for ch in allowed[pivot] for w in BY_POS[pivot][ch]
                          if all(w[c] in allowed[c] for c in range(5))]
            random.shuffle(candidates)
            for word in candidates:
                if word in rows:
                    continue
                rows.append(word)
                found = search()
                if found:
                    return found
                rows.pop()
                if nodes > max_nodes:
                    return None
            return None

        result = search()
        if result:
            validate_board(result)
            return result
    raise RuntimeError("Could not generate a puzzle from the bundled answer list.")


def _load_board_list() -> list[list[str]]:
    """All possible boards, precomputed by tools/build_board_list.py (458 for the current list).

    Boards are re-validated against the current answer list at startup, so editing answers.txt
    without rebuilding the file can only remove boards, never serve an invalid one.
    """
    path = ROOT / "all_boards.json"
    if not path.exists():
        log.warning("all_boards.json missing; falling back to live board generation")
        return []
    boards = []
    for rows in json.loads(path.read_text()):
        try:
            if all(w in ANSWER_WORDS for w in rows):
                validate_board(rows)
                boards.append(rows)
        except ValueError:
            pass
    log.info("loaded %d precomputed boards", len(boards))
    return boards


ALL_BOARDS = _load_board_list()


def pick_board(con: sqlite3.Connection) -> list[str]:
    """Pick a random board, preferring ones this server has not dealt yet.

    Instant (no search). Once every board has been used, any board may repeat.
    """
    if not ALL_BOARDS:
        return generate_board()
    used = {r[0] for r in con.execute("SELECT DISTINCT rows_json FROM generated_boards")}
    fresh = [b for b in ALL_BOARDS if json.dumps(b) not in used]
    return list(random.choice(fresh or ALL_BOARDS))


def new_state():
    return {
        "guesses_left": START_GUESSES, "bonus_earned": 0,
        "row": 0, "col": 0, "ended": False, "won": False,
        "solved_rows": [], "solved_cols": [], "history": [],
        "cells": [[[] for _ in range(5)] for _ in range(5)],
    }


def get_game(game_id: str, con: sqlite3.Connection | None = None):
    if con is None:
        with db() as own:
            return get_game(game_id, own)
    row = con.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
    board_row = con.execute("SELECT rows_json FROM generated_boards WHERE id=?", (row["board_id"],)).fetchone() if row else None
    if not row:
        raise HTTPException(404, "Game not found.")
    if board_row:
        rows = json.loads(board_row["rows_json"])
    else:
        # Backward compatibility for games created by the original dated-board MVP.
        legacy_path = ROOT / "boards.json"
        legacy = json.loads(legacy_path.read_text()) if legacy_path.exists() else {}
        if row["board_id"] not in legacy:
            raise HTTPException(410, "This saved game references a puzzle that is no longer available.")
        rows = legacy[row["board_id"]]["rows"]
    return row, json.loads(row["state_json"]), rows


def save_state(game_id: str, state: dict[str, Any], con: sqlite3.Connection):
    now = datetime.now(timezone.utc).isoformat()
    con.execute("UPDATE games SET state_json=?, updated_at=? WHERE id=?", (json.dumps(state), now, game_id))


def is_green(cell):
    return any(clue["color"] == "green" for clue in cell)


def line_solved(state, axis: str, idx: int):
    if axis == "row":
        return all(is_green(state["cells"][idx][c]) for c in range(5))
    return all(is_green(state["cells"][r][idx]) for r in range(5))


def next_pair(state) -> int:
    """Row and column always share one index, so the crossing is always on the diagonal.

    Move to the next index whose row OR column is still unsolved; an index is skipped only
    once both its row and its column are solved. (If just one is solved, the guess still
    fills both, but the solved line's green squares take no new tiles.)
    """
    current = state["row"]
    for offset in range(1, 6):
        i = (current + offset) % 5
        if not (line_solved(state, "row", i) and line_solved(state, "col", i)):
            return i
    return current


def line_feedback(guess: str, answer: str) -> list[int]:
    answer_counts, guess_counts = Counter(answer), Counter(guess)
    return [min(answer_counts[ch], guess_counts[ch]) for ch in guess]


def classify(ch: str, row_answer: str, col_answer: str, board_letters: set[str], row_count: int, col_count: int):
    if ch == row_answer and ch == col_answer:
        return "green"
    in_row, in_col = row_count > 0, col_count > 0
    if in_row and in_col: return "orange"
    if in_row: return "yellow"
    if in_col: return "red"
    return "white" if ch in board_letters else "black"


def public_state(state, answers: list[str] | None = None):
    # Full append-only clue history for every square. The solution is revealed
    # only once the game has ended, never before.
    out = {
        "guesses_left": state["guesses_left"], "bonus_earned": state["bonus_earned"],
        "row": state["row"], "col": state["col"], "ended": state["ended"], "won": state["won"],
        "solved_rows": state["solved_rows"], "solved_cols": state["solved_cols"],
        "history": state["history"], "cells": state["cells"],
        "words_solved": len(state["solved_rows"]) + len(state["solved_cols"]),
    }
    if state["ended"] and answers:
        out["solution"] = [w.upper() for w in answers]
    return out


class GuessRequest(BaseModel):
    word: str = Field(min_length=5, max_length=5, pattern=r"^[A-Za-z]{5}$")


_create_hits: dict[str, deque] = defaultdict(deque)
_create_lock = threading.Lock()


def _rate_limit_create(request: Request):
    """Generation costs CPU, so cap how fast one client can create games."""
    if CREATE_LIMIT_PER_MIN <= 0:
        return
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    ip = fwd or (request.client.host if request.client else "unknown")
    now = time.monotonic()
    with _create_lock:
        hits = _create_hits[ip]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= CREATE_LIMIT_PER_MIN:
            raise HTTPException(429, "You're starting puzzles very quickly. Give it a moment and try again.")
        hits.append(now)
        if len(_create_hits) > 5000:  # bound memory
            for key in [k for k, v in _create_hits.items() if not v or now - v[-1] > 60]:
                _create_hits.pop(key, None)


def _check_id(game_id: str) -> str:
    try:
        return str(uuid.UUID(game_id))
    except ValueError:
        raise HTTPException(404, "Game not found.")


@app.get("/api/health")
def health():
    with db() as con:
        con.execute("SELECT 1").fetchone()
    return {"status": "ok", "version": app.version, "answers": len(ANSWER_WORDS), "accepted": len(VALID_WORDS)}


@app.get("/api/weekly")
def game_info():
    # Kept as a compatibility route for the existing browser client.
    return {"mode": "freeplay", "title": "Freeplay Squardle", "rules": {"starting_guesses": START_GUESSES, "bonus_guesses": BONUS_LIMIT, "words": 10}}


def _create_with_rows(rows: list[str]):
    board_id, game_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    state = new_state()
    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO generated_boards VALUES (?,?,?)", (board_id, json.dumps(rows), now))
        con.execute("INSERT INTO games VALUES (?,?,?,?,?)", (game_id, board_id, json.dumps(state), now, now))
        con.execute("COMMIT")
    return {"game_id": game_id, "state": public_state(state)}


@app.post("/api/test-game")
def create_test_game(request: Request):
    if not TEST_MODE:
        raise HTTPException(404, "Not found.")
    _rate_limit_create(request)
    validate_board(TEST_BOARD)
    return _create_with_rows(list(TEST_BOARD))


@app.post("/api/game")
def create_game(request: Request):
    _rate_limit_create(request)
    with db() as con:
        rows = pick_board(con)
    return _create_with_rows(rows)


@app.get("/api/game/{game_id}")
def resume_game(game_id: str):
    _, state, answers = get_game(_check_id(game_id))
    return {"game_id": game_id, "state": public_state(state, answers)}


def _apply_guess(state: dict[str, Any], word: str, answers: list[str]) -> dict[str, Any]:
    guess = word.upper()
    answers = [w.upper() for w in answers]
    cols = ["".join(answer[c] for answer in answers) for c in range(5)]
    if state["row"] != state["col"]:  # saved before the paired-index rule
        state["col"] = state["row"]
    r, c = state["row"], state["col"]
    h_counts, v_counts = line_feedback(guess, answers[r]), line_feedback(guess, cols[c])
    board_letters = set("".join(answers))
    guess_id = len(state["history"]) + 1

    def append_clue(rr, cc, ch, row_arrows, col_arrows, axis):
        cell = state["cells"][rr][cc]
        if is_green(cell):
            return
        color = classify(ch, answers[rr][cc], cols[cc][rr], board_letters, row_arrows, col_arrows)
        cell.append({"id": f"{guess_id}:{axis}", "guess_id": guess_id, "axis": axis,
                     "letter": ch.upper(), "color": color,
                     "row_arrows": row_arrows, "col_arrows": col_arrows})

    # When row == col the crossing square receives the same letter from both
    # directions, so it gets ONE tile carrying both the row and column counts.
    # (If row != col the two letters differ and each keeps its own tile.)
    merged = r == c
    for i, ch in enumerate(guess):
        if merged and i == c:
            append_clue(r, c, ch, h_counts[c], v_counts[r], "cross")
        else:
            append_clue(r, i, ch, h_counts[i], min(cols[i].count(ch), 1), "across")
    for i, ch in enumerate(guess):
        if merged and i == r:
            continue
        append_clue(i, c, ch, min(answers[i].count(ch), 1), v_counts[i], "down")

    newly = []
    for i in range(5):
        if i not in state["solved_rows"] and line_solved(state, "row", i):
            state["solved_rows"].append(i); newly.append(f"row {i+1}")
        if i not in state["solved_cols"] and line_solved(state, "col", i):
            state["solved_cols"].append(i); newly.append(f"column {i+1}")
    # The first eight completed words award one bonus each; total bonus is capped at eight.
    bonus_awarded = min(len(newly), BONUS_LIMIT - state["bonus_earned"])
    state["bonus_earned"] += bonus_awarded
    state["guesses_left"] = state["guesses_left"] - 1 + bonus_awarded
    state["history"].insert(0, {"word": guess, "solved": newly, "bonus_awarded": bonus_awarded})
    state["won"] = len(state["solved_rows"]) + len(state["solved_cols"]) == 10
    state["ended"] = state["won"] or state["guesses_left"] <= 0
    if not state["ended"]:
        state["row"] = state["col"] = next_pair(state)
    return {"word": guess, "solved": newly, "bonus_awarded": bonus_awarded}


@app.post("/api/game/{game_id}/guess")
def make_guess(game_id: str, request: GuessRequest):
    game_id = _check_id(game_id)
    word = request.word.lower()
    if word not in VALID_WORDS:
        raise HTTPException(422, "Not in word list.")
    with db() as con:
        # Serialize concurrent guesses on the same game (double taps, two tabs).
        con.execute("BEGIN IMMEDIATE")
        try:
            _, state, answers = get_game(game_id, con)
            if state["ended"]:
                raise HTTPException(409, "This game has ended.")
            last = _apply_guess(state, word, answers)
            save_state(game_id, state, con)
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
    return public_state(state, answers) | {"last_guess": last}


FRONTEND = ROOT.parent / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
