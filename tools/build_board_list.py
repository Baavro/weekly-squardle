"""Build backend/all_boards.json: every possible Squardle board for the current answer list.

A board is 5 row words + 5 column words, all from backend/answers.txt, all 10 different.
Same algorithm as tools/count_boards.c (row-by-row search with column-prefix pruning):

  * Keep, for every column, the prefix formed by the rows placed so far.
  * A word can be the next row only if, for every column c, prefix_c + word[c] is still the
    start of some answer word. We precompute the set of valid prefixes, so that test is a
    set lookup, and we narrow candidates with an index of words by (position, letter).
  * After 5 rows every column prefix is a full word; keep the board if all 10 words differ.

Run from the repo root (takes about a minute; tools/count_boards.c does it in under a second):

    python tools/build_board_list.py            # writes backend/all_boards.json
    python tools/build_board_list.py --check    # just prints the count
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANSWERS = ROOT / "backend" / "answers.txt"
OUT = ROOT / "backend" / "all_boards.json"


def all_boards(words: list[str]) -> list[list[str]]:
    words = sorted(set(words))
    word_set = set(words)
    prefixes = {w[:k] for w in words for k in range(6)}
    # Letters that can follow each prefix (e.g. "ab" -> {"b", "o", ...}).
    next_letters: dict[str, set[str]] = defaultdict(set)
    for w in words:
        for k in range(5):
            next_letters[w[:k]].add(w[k])
    # by_pos[c][letter] = words with that letter at position c
    by_pos = [defaultdict(set) for _ in range(5)]
    for w in words:
        for c, ch in enumerate(w):
            by_pos[c][ch].add(w)

    found: list[list[str]] = []
    rows: list[str] = []

    def search() -> None:
        d = len(rows)
        if d == 5:
            cols = ["".join(r[c] for r in rows) for c in range(5)]
            if all(col in word_set for col in cols) and len(set(rows + cols)) == 10:
                found.append(rows.copy())
            return
        col_prefixes = ["".join(r[c] for r in rows) for c in range(5)]
        # Candidates: words whose letter in each column can extend that column's prefix.
        cand: set[str] | None = None
        for c in sorted(range(5), key=lambda c: len(next_letters[col_prefixes[c]])):
            allowed = set().union(*(by_pos[c][ch] for ch in next_letters[col_prefixes[c]])) if next_letters[col_prefixes[c]] else set()
            cand = allowed if cand is None else cand & allowed
            if not cand:
                return
        for w in sorted(cand):
            if all(col_prefixes[c] + w[c] in prefixes for c in range(5)):
                rows.append(w)
                search()
                rows.pop()

    search()
    return found


def main() -> None:
    words = [w.strip().lower() for w in ANSWERS.read_text().split() if len(w.strip()) == 5]
    boards = all_boards(words)
    print(f"{len(words)} answer words -> {len(boards)} possible boards "
          f"({len(boards) // 2} mirror pairs: rows and columns swapped)")
    if "--check" not in sys.argv:
        OUT.write_text(json.dumps(boards, separators=(",", ":")) + "\n")
        print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
