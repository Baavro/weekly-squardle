# Squardle — Freeplay

A server-authoritative, freeplay implementation of the 5×5 interlocking-word game. Every new game asks the backend to generate a fresh grid of five across and five down words (ten distinct words). The API never returns the solution until a game has ended.

## Features

- 5×5 grid, 10 starting guesses, up to 8 bonus guesses (one per each of the first eight solved words)
- Guess fills the highlighted row **and** column; the crossing square (always on the diagonal) gets one clue tile
- The latest guess shows full size for 5 s (or until you start typing), then collapses into small history chips; only greens stay full size
- Duplicate-aware arrow counts; row and column always share an index, cycling past a pair only once both its row and column are solved
- Full clue history, tap-to-fade/hide tiles, long-press/right-click to highlight a letter, tap a past guess to highlight its tiles, full-square pencil notes
- Colour-blind palette with symbols, reduced-motion support, keyboard + touch input, screen-reader announcements
- Local stats and streaks, share-as-emoji-grid, answer reveal when a game is lost
- Saved game in browser storage and SQLite (WAL); concurrent guesses are serialized per game

## Run locally

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000
```

Open http://localhost:8000.

## Word lists

- `backend/answers.txt` — ~2,500 common words used to build puzzles (no names, brands or vulgar words).
- `backend/words.txt` — ~14,900 accepted guesses.

Rebuild both with `pip install wordfreq && python tools/build_wordlists.py <five-letter-list.txt>`. Review the licence of whichever source list you use before commercial redistribution.

## API

- `GET /api/health`
- `GET /api/weekly` — compatibility metadata route (`mode: freeplay`)
- `POST /api/game` — generate a puzzle and create a game (rate-limited per IP)
- `GET /api/game/{game_id}` — resume a game
- `POST /api/game/{game_id}/guess` with `{"word":"CRANE"}`
- `POST /api/test-game` — **local QA only**, returns 404 unless `SQUARDLE_TEST_MODE=1`; always deals the fixed test board. Open `http://localhost:8000/?test` (or run `make test-game`).

## Configuration

See `.env.example`: `DATABASE_PATH`, `CORS_ORIGINS` (only for a split frontend/API), `GAME_TTL_DAYS`, `CREATE_LIMIT_PER_MIN`.

## Production notes

- Strict CSP (no inline scripts/styles), `nosniff`, frame denial, `no-store` on the API, gzip.
- Run **one** instance with a persistent disk (`render.yaml` mounts `/var/data`; free-tier disks are ephemeral). For multiple replicas, move storage to PostgreSQL and the rate limiter to a shared store.
- Behind a proxy, run uvicorn with `--proxy-headers` so rate limiting sees client IPs (already set in `render.yaml` and the Dockerfile).
- Games untouched for `GAME_TTL_DAYS` are purged at startup.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```
