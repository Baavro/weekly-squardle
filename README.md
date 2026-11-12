# Weekly Squardle — production MVP scaffold

A server-authoritative implementation of the Weekly Squardle rules. The browser never receives the answer grid; guesses are validated and scored by FastAPI. SQLite persists game state, and the static frontend is served from the same origin.

## Included

- Responsive browser client and on-screen keyboard
- 5×5 board with 5 row words + 5 column words
- 10 starting guesses and up to 8 bonus guesses
- First eight completed words award one bonus guess each; the final two do not
- Weekly cycling rule: if exactly one unsolved row/column has more than one unresolved square, the next guess is directed there
- Duplicate-aware directional clue counts
- Green squares persist; latest clue for each other square is shown
- Server-side answer storage, accepted-word validation, resumable game IDs
- Docker deployment and a basic health endpoint

## Run locally

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000
```

Open http://localhost:8000. Health check: http://localhost:8000/api/health.

## Deploy with Docker

Build from the project root (the directory containing this Dockerfile):

```bash
docker build -t weekly-squardle .
docker run -d --name weekly-squardle \\
  -p 8000:8000 \\
  -v squardle-data:/data \\
  -e CORS_ORIGINS=https://your-game-domain.example \\
  weekly-squardle
```

Put the service behind HTTPS using a managed reverse proxy/load balancer. Keep `/data` on a persistent volume and back it up. For multiple application replicas, replace SQLite with PostgreSQL before scaling horizontally. Add rate limiting at the edge for `/api/game/*/guess` and `/api/game`.

## Publishing a weekly puzzle

Edit `backend/boards.json` and add a board with a Sunday `week_starts` date:

```json
{
  "2026-11-15": {
    "title": "Weekly Squardle · Week of November 15",
    "week_starts": "2026-11-15",
    "rows": ["FIRST", "...", "...", "...", "..."]
  }
}
```

The five rows define the 25-square answer grid. The five vertical words are derived from those letters. Before publishing, verify all ten words are accepted five-letter English words and are distinct. The included board for 2026-11-08 is a demonstration board only. A real release should be editorially curated and play-tested; do not reuse the demonstration puzzle as a live commercial weekly challenge.

The API validates each published board at game creation/guess time. Invalid rows or columns fail closed rather than silently publishing a broken puzzle.

## API

- `GET /api/health`
- `GET /api/weekly` — public metadata only, no answers
- `POST /api/game` — create a game
- `GET /api/game/{game_id}` — resume a game
- `POST /api/game/{game_id}/guess` with `{"word":"CRANE"}`

The game UUID is a bearer capability for that game. Do not expose it in public URLs or analytics. Current MVP stores games without user accounts; add identity and abuse controls if cross-device sync, leaderboards, or prizes are required.

## Release gates before public launch

1. Have a human editor approve a rolling bank of weekly answer grids and a separate answer/guess vocabulary policy.
2. Run the rule test suite and add golden tests against official sample positions, especially duplicate-letter arrow counts and crossing squares.
3. Confirm the intended weekly reset timezone and set `WEEKLY_TIMEZONE` accordingly (default: UTC). The service fails closed if the current Sunday board is missing or invalid.
4. Move to PostgreSQL if deploying multiple replicas; add migrations, automated backups, rate limits, monitoring, error reporting, and abuse protection.
5. Add accessibility review (keyboard navigation, screen-reader labels, non-colour clue symbols) and mobile device testing.
6. Review trademark/branding and obtain permission before presenting this as an official FubarGames product. This implementation is independent and unaffiliated.

## Word list

`backend/words.txt` is a bundled 5-letter CMU Pronouncing Dictionary-derived guess list. It is intentionally broad and can include uncommon words; the weekly answer grid should be curated separately. Review the CMU dictionary license and attribution requirements before redistribution in a commercial deployment.
