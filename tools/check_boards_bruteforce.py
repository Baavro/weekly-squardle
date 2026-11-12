"""Deliberately simple, independent counter used to double-check tools/count_boards.c.

Tries every word as the next row and keeps it only if each column is still the start of a word.
Slow on the full list (minutes) but easy to verify by eye. Usage:
    python tools/check_boards_bruteforce.py backend/answers.txt
"""
import sys
words = [w.strip() for w in open(sys.argv[1]) if len(w.strip()) == 5]
wset = set(words)
prefixes = {w[:k] for w in words for k in range(6)}
leaves = boards = 0
def rec(rows):
    global leaves, boards
    d = len(rows)
    if d == 5:
        cols = ["".join(r[c] for r in rows) for c in range(5)]
        if all(c in wset for c in cols):
            leaves += 1
            if len(set(rows + cols)) == 10: boards += 1
        return
    for w in words:
        if all("".join(r[c] for r in rows) + w[c] in prefixes for c in range(5)):
            rec(rows + [w])
rec([])
print(f"words={len(words)} leaves={leaves} boards={boards}")
