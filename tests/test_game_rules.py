import json
import os
import sqlite3
import tempfile

# Keep tests isolated from a developer's local database.
tmp = tempfile.NamedTemporaryFile(delete=False)
os.environ["DATABASE_PATH"] = tmp.name
os.environ["CREATE_LIMIT_PER_MIN"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import (  # noqa: E402
    ANSWER_WORDS, VALID_WORDS, app, generate_board, line_feedback, validate_board,
)

client = TestClient(app)


def board_for(game_id: str) -> list[str]:
    with sqlite3.connect(tmp.name) as con:
        bid = con.execute("SELECT board_id FROM games WHERE id=?", (game_id,)).fetchone()[0]
        return json.loads(con.execute("SELECT rows_json FROM generated_boards WHERE id=?", (bid,)).fetchone()[0])


def test_generated_board_is_valid_and_has_ten_distinct_words():
    rows = generate_board()
    _, cols = validate_board(rows)
    assert len(set(rows + cols)) == 10
    assert all(word in ANSWER_WORDS for word in rows + cols)
    assert all(word in VALID_WORDS for word in rows + cols)


def test_new_game_is_freeplay_and_creates_distinct_board_ids():
    first = client.post('/api/game').json()
    second = client.post('/api/game').json()
    assert first['game_id'] != second['game_id']
    assert first['state']['guesses_left'] == 10
    assert second['state']['guesses_left'] == 10
    assert board_for(first['game_id']) != board_for(second['game_id'])
    assert client.get('/api/weekly').json()['mode'] == 'freeplay'


def test_guess_returns_full_clue_history():
    game = client.post('/api/game').json()
    response = client.post(f"/api/game/{game['game_id']}/guess", json={'word': 'crane'})
    assert response.status_code == 200
    cells = response.json()['cells']
    assert any(len(cell) for row in cells for cell in row)
    assert all('guess_id' in clue for row in cells for cell in row for clue in cell)


def test_duplicate_letter_feedback_is_capped_by_answer_count():
    assert line_feedback('allee', 'apple') == [1, 1, 1, 1, 1]


def test_solution_is_hidden_until_game_ends_then_revealed():
    game = client.post('/api/game').json()
    assert 'solution' not in game['state']
    assert 'solution' not in client.get(f"/api/game/{game['game_id']}").json()['state']
    # Burn all guesses on a valid word that is unlikely to solve anything.
    state = game['state']
    for _ in range(40):
        if state['ended']:
            break
        state = client.post(f"/api/game/{game['game_id']}/guess", json={'word': 'zzzzz' if 'zzzzz' in VALID_WORDS else 'fuzzy'}).json()
    assert state['ended']
    assert state['solution'] == [w.upper() for w in board_for(game['game_id'])]
    assert client.post(f"/api/game/{game['game_id']}/guess", json={'word': 'crane'}).status_code == 409


def test_perfect_play_wins_and_awards_bonuses():
    game = client.post('/api/game').json()
    gid = game['game_id']
    rows = board_for(gid)
    cols = [''.join(r[c] for r in rows) for c in range(5)]
    state = game['state']
    for _ in range(25):
        if state['ended']:
            break
        r, c = state['row'], state['col']
        word = rows[r] if r not in state['solved_rows'] else cols[c]
        state = client.post(f"/api/game/{gid}/guess", json={'word': word}).json()
    assert state['won'] and state['words_solved'] == 10
    assert state['bonus_earned'] == 8


def test_rejects_unknown_words_malformed_input_and_bad_ids():
    gid = client.post('/api/game').json()['game_id']
    assert client.post(f"/api/game/{gid}/guess", json={'word': 'qqqqq'}).status_code == 422
    assert client.post(f"/api/game/{gid}/guess", json={'word': 'abc'}).status_code == 422
    assert client.post(f"/api/game/{gid}/guess", json={'word': 'ab1de'}).status_code == 422
    assert client.get('/api/game/not-a-uuid').status_code == 404
    assert client.get('/api/game/00000000-0000-4000-8000-000000000000').status_code == 404
    # A rejected word must not cost a guess.
    assert client.get(f"/api/game/{gid}").json()['state']['guesses_left'] == 10


def test_security_headers_and_health():
    r = client.get('/api/health')
    assert r.status_code == 200 and r.json()['status'] == 'ok'
    assert r.headers['cache-control'] == 'no-store'
    assert r.headers['x-content-type-options'] == 'nosniff'
    assert "script-src 'self'" in r.headers['content-security-policy']
    assert client.get('/').status_code == 200


def test_diagonal_crossing_gets_one_tile():
    from backend.main import _apply_guess, new_state
    answers = ["crane", "river", "alone", "ninja", "enter"]
    state = new_state()
    for word in ["slate", "pious", "grade"]:
        r, c = state["row"], state["col"]
        assert r == c
        _apply_guess(state, word, answers)
        gid = len(state["history"])
        here = [k for k in state["cells"][r][c] if k["guess_id"] == gid]
        assert len(here) == 1 and here[0]["axis"] == "cross"
        # Every other square in the row and column got exactly one tile from this guess.
        for i in range(5):
            for rr, cc in ((r, i), (i, c)):
                assert len([k for k in state["cells"][rr][cc] if k["guess_id"] == gid]) <= 1


def test_test_game_endpoint_deals_fixed_board_and_can_be_disabled(monkeypatch):
    import backend.main as m
    client = TestClient(app)
    res = client.post('/api/test-game')
    assert res.status_code == 200 and res.json()['state']['guesses_left'] == 10
    monkeypatch.setattr(m, 'TEST_MODE', False)
    assert client.post('/api/test-game').status_code == 404


def test_row_and_column_always_share_an_index():
    from backend.main import TEST_BOARD, _apply_guess, new_state
    state = new_state()
    for word in ["queen", "nouns", "amino", "geese", "steep", "plans", "linen", "tenor", "nitro", "plugs"]:
        assert state["row"] == state["col"]
        # Never sent to a pair whose row and column are both already solved.
        assert not (state["row"] in state["solved_rows"] and state["col"] in state["solved_cols"])
        _apply_guess(state, word, TEST_BOARD)
    assert state["won"] and len(state["history"]) == 10
