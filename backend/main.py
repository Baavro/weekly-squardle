from __future__ import annotations
import json, os, sqlite3, uuid
from collections import Counter
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv('DATABASE_PATH', ROOT / 'squardle.sqlite3'))
BOARDS_PATH = Path(os.getenv('BOARDS_PATH', ROOT / 'boards.json'))
WORDS_PATH = ROOT / 'words.txt'
START_GUESSES, BONUS_LIMIT = 10, 8

with WORDS_PATH.open() as f:
    VALID_WORDS = {line.strip().lower() for line in f if len(line.strip()) == 5}

# Solutions are deliberately server-side. Never include this object in public API responses.
BOARDS = json.loads(BOARDS_PATH.read_text())

app = FastAPI(title='Weekly Squardle API', version='1.0.0')
allowed_origins = [x.strip() for x in os.getenv('CORS_ORIGINS', 'http://localhost:8000').split(',') if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_credentials=False, allow_methods=['GET','POST'], allow_headers=['Content-Type'])


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS games (
            id TEXT PRIMARY KEY, board_id TEXT NOT NULL, state_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )''')
        con.execute('CREATE INDEX IF NOT EXISTS idx_games_updated ON games(updated_at)')

init_db()


def active_board():
    # Weekly boards go live Sunday at midnight in the configured game timezone.
    local_today = datetime.now(ZoneInfo(os.getenv('WEEKLY_TIMEZONE', 'UTC'))).date()
    sunday = local_today - timedelta(days=(local_today.weekday() + 1) % 7)
    key = sunday.isoformat()
    board = BOARDS.get(key)
    if not board:
        raise HTTPException(503, 'This week\'s puzzle has not been published yet.')
    try:
        validate_board(board)
    except ValueError as exc:
        raise HTTPException(503, f'This week\'s puzzle is not publishable: {exc}')
    return key, board


def validate_board(board: dict[str, Any]):
    rows = board.get('rows', [])
    if len(rows) != 5 or any(len(w) != 5 or w.lower() not in VALID_WORDS for w in rows):
        raise ValueError('Every row must be a valid five-letter word.')
    cols = [''.join(row[c] for row in rows) for c in range(5)]
    if any(w.lower() not in VALID_WORDS for w in cols):
        raise ValueError(f'Invalid column word in board: {cols}')
    if len(set(rows + cols)) != 10:
        raise ValueError('Weekly puzzle must contain 10 distinct words.')
    return rows, cols


def new_state():
    return {
        'guesses_left': START_GUESSES, 'bonus_earned': 0,
        'row': 0, 'col': 0, 'ended': False, 'won': False,
        'solved_rows': [], 'solved_cols': [], 'history': [],
        'cells': [[[] for _ in range(5)] for _ in range(5)]
    }


def get_state(game_id: str):
    with db() as con:
        row = con.execute('SELECT * FROM games WHERE id=?', (game_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Game not found.')
    return row, json.loads(row['state_json'])


def save_state(game_id: str, state: dict):
    now = datetime.now(timezone.utc).isoformat()
    with db() as con:
        con.execute('UPDATE games SET state_json=?, updated_at=? WHERE id=?', (json.dumps(state), now, game_id))


def line_solved(state, axis: str, idx: int):
    if axis == 'row':
        return all(any(item['color'] == 'green' for item in state['cells'][idx][c]) for c in range(5))
    return all(any(item['color'] == 'green' for item in state['cells'][r][idx]) for r in range(5))


def unknown_count(state, axis: str, idx: int):
    if axis == 'row':
        return sum(not any(item['color'] == 'green' for item in state['cells'][idx][c]) for c in range(5))
    return sum(not any(item['color'] == 'green' for item in state['cells'][r][idx]) for r in range(5))


def next_line(state, axis: str):
    # Weekly kindness rule: force the sole line with >1 unresolved squares.
    candidates = [i for i in range(5) if not line_solved(state, axis, i) and unknown_count(state, axis, i) > 1]
    if len(candidates) == 1:
        return candidates[0]
    current = state[axis]
    for offset in range(1, 6):
        candidate = (current + offset) % 5
        if not line_solved(state, axis, candidate):
            return candidate
    return current


def line_feedback(guess: str, answer: str) -> list[int]:
    # Count matching letters per line, including duplicates; this is the arrow multiplicity.
    answer_counts, guess_counts = Counter(answer), Counter(guess)
    return [min(answer_counts[ch], guess_counts[ch]) for ch in guess]


def classify(ch: str, r: int, c: int, row_answer: str, col_answer: str, board_letters: set[str], row_count: int, col_count: int):
    if ch == row_answer[c] and ch == col_answer[r]:
        return 'green'
    in_row, in_col = row_count > 0, col_count > 0
    if in_row and in_col: return 'orange'
    if in_row: return 'yellow'
    if in_col: return 'red'
    return 'white' if ch in board_letters else 'black'


def public_state(state):
    # Never return the answer grid.
    cells = []
    for row in state['cells']:
        cells.append([cell[-1] if cell else None for cell in row])
    return {
        'guesses_left': state['guesses_left'], 'bonus_earned': state['bonus_earned'],
        'row': state['row'], 'col': state['col'], 'ended': state['ended'], 'won': state['won'],
        'solved_rows': state['solved_rows'], 'solved_cols': state['solved_cols'],
        'history': state['history'], 'cells': cells,
        'words_solved': len(state['solved_rows']) + len(state['solved_cols'])
    }


class GuessRequest(BaseModel):
    word: str = Field(min_length=5, max_length=5, pattern=r'^[A-Za-z]{5}$')


@app.get('/api/health')
def health():
    return {'status': 'ok', 'version': app.version}


@app.get('/api/weekly')
def weekly():
    board_id, board = active_board()
    return {'board_id': board_id, 'title': board.get('title', 'Weekly Squardle'), 'week_starts': board.get('week_starts'), 'rules': {'starting_guesses': START_GUESSES, 'bonus_guesses': BONUS_LIMIT, 'words': 10}}


@app.post('/api/game')
def create_game():
    board_id, _ = active_board()
    game_id = str(uuid.uuid4())
    state = new_state()
    now = datetime.now(timezone.utc).isoformat()
    with db() as con:
        con.execute('INSERT INTO games VALUES (?,?,?,?,?)', (game_id, board_id, json.dumps(state), now, now))
    return {'game_id': game_id, 'state': public_state(state)}


@app.get('/api/game/{game_id}')
def resume_game(game_id: str):
    _, state = get_state(game_id)
    return {'game_id': game_id, 'state': public_state(state)}


@app.post('/api/game/{game_id}/guess')
def make_guess(game_id: str, request: GuessRequest):
    row, state = get_state(game_id)
    if state['ended']:
        raise HTTPException(409, 'This game has ended.')
    word = request.word.lower()
    if word not in VALID_WORDS:
        raise HTTPException(422, 'That is not in the accepted five-letter word list.')
    guess = word.upper()
    board = BOARDS[row['board_id']]
    answers, cols = validate_board(board)
    r, c = state['row'], state['col']
    h_counts = line_feedback(guess, answers[r])
    v_counts = line_feedback(guess, cols[c])
    board_letters = set(''.join(answers))

    def put(rr, cc, ch, row_arrows, col_arrows):
        cell = state['cells'][rr][cc]
        # A solved square is permanent, just like the original game.
        if any(item['color'] == 'green' for item in cell):
            return
        color = classify(ch, rr, cc, answers[rr], cols[cc], board_letters, row_arrows, col_arrows)
        cell.append({'letter': ch.upper(), 'color': color,
                     'row_arrows': row_arrows, 'col_arrows': col_arrows})

    # The horizontal word contributes five row guesses. The perpendicular clue
    # is a single-letter membership hint for each corresponding column.
    for i, ch in enumerate(guess):
        row_arrows = h_counts[i]
        col_arrows = min(cols[i].count(ch), 1)
        put(r, i, ch, row_arrows, col_arrows)

    # The vertical word contributes five column guesses. The perpendicular clue
    # is a single-letter membership hint for each corresponding row.
    for i, ch in enumerate(guess):
        if i == r and c == c:  # shared crossing was already handled below
            continue
        row_arrows = min(answers[i].count(ch), 1)
        col_arrows = v_counts[i]
        put(i, c, ch, row_arrows, col_arrows)

    # Re-evaluate the crossing with full duplicate-aware counts in both directions.
    cross_cell = state['cells'][r][c]
    if not any(item['color'] == 'green' for item in cross_cell):
        cross_color = classify(guess[c], r, c, answers[r], cols[c], board_letters, h_counts[c], v_counts[r])
        cross_cell.append({'letter': guess[c], 'color': cross_color,
                           'row_arrows': h_counts[c], 'col_arrows': v_counts[r]})

    newly = []
    for i in range(5):
        if i not in state['solved_rows'] and line_solved(state, 'row', i):
            state['solved_rows'].append(i); newly.append(f'row {i+1}')
        if i not in state['solved_cols'] and line_solved(state, 'col', i):
            state['solved_cols'].append(i); newly.append(f'column {i+1}')
    bonus_awarded = min(len(newly), BONUS_LIMIT - state['bonus_earned'])
    state['bonus_earned'] += bonus_awarded
    state['guesses_left'] = state['guesses_left'] - 1 + bonus_awarded
    state['history'].insert(0, {'word': guess, 'solved': newly, 'bonus_awarded': bonus_awarded})
    state['won'] = len(state['solved_rows']) + len(state['solved_cols']) == 10
    state['ended'] = state['won'] or state['guesses_left'] <= 0
    if not state['ended']:
        state['row'] = next_line(state, 'row')
        state['col'] = next_line(state, 'col')
    save_state(game_id, state)
    response = public_state(state)
    response['last_guess'] = {'word': guess, 'solved': newly, 'bonus_awarded': bonus_awarded}
    return response


# Serve the browser client from the same origin in production.
FRONTEND = ROOT.parent / 'frontend'
if FRONTEND.exists():
    app.mount('/', StaticFiles(directory=FRONTEND, html=True), name='frontend')
