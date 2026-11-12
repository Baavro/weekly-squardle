import unittest
from backend.main import line_feedback, validate_board, VALID_WORDS, new_state, next_line

class WeeklySquardleRules(unittest.TestCase):
    def test_duplicate_letter_arrow_count(self):
        # Official rules example: GEESE vs LEVEL. Both guessed Es are counted,
        # even though one E is already in its exact position.
        counts = line_feedback('LEVEL', 'GEESE')
        self.assertEqual(counts[1], 2)
        self.assertEqual(counts[3], 2)

    def test_demo_board_has_ten_valid_distinct_words(self):
        board = {'rows': ['PANSY', 'ALOHA', 'GATOR', 'AMEND', 'NODES']}
        rows, cols = validate_board(board)
        self.assertEqual(cols, ['PAGAN', 'ALAMO', 'NOTED', 'SHONE', 'YARDS'])
        self.assertEqual(len(set(rows + cols)), 10)
        self.assertTrue(all(word.lower() in VALID_WORDS for word in rows + cols))

    def test_weekly_cycle_forces_only_multi_unknown_line(self):
        state = new_state()
        # Mark all but two cells in row 3 as solved.
        for c in [0, 1, 2]:
            state['cells'][2][c].append({'color': 'green'})
        # Other rows still have five unknowns, so this is not the sole candidate.
        self.assertNotEqual(next_line(state, 'row'), 2)
        # Make every other row either solved or down to one unknown square.
        for r in [0, 1, 3, 4]:
            for c in [0, 1, 2, 3]:
                state['cells'][r][c].append({'color': 'green'})
        self.assertEqual(next_line(state, 'row'), 2)

if __name__ == '__main__':
    unittest.main()
