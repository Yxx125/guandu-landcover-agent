"""Focused, offline checks for interval-specific outgoing land classes."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_query_router import _compare_explicit_outgoing_periods  # noqa: E402


def matrix(start, end):
    rows = {(source, target): {"pixel_count": 0}
            for source in range(1, 10) for target in range(1, 10)}
    rows[(1, 2)]["pixel_count"] = 100 if start == 2023 else 30
    rows[(2, 1)]["pixel_count"] = 200 if start == 2016 else 20
    return rows


class OutgoingPeriodsTest(unittest.TestCase):
    @patch("agent_query_router._matrix", side_effect=matrix)
    def test_different_classes_are_bound_to_their_intervals(self, mocked):
        result = _compare_explicit_outgoing_periods(
            "2023到2025年的耕地转出多还是2016年到2025年林地转出的多")
        self.assertEqual([(r["start_year"], r["end_year"], r["class_code"],
                           r["pixel_count"]) for r in result["facts"]],
                         [(2023, 2025, 1, 100), (2016, 2025, 2, 200)])
        self.assertIn("2016→2025 年林地转出更多", result["answer"])
        self.assertIn("0.0900 km²", result["answer"])
        self.assertEqual(result["query"]["class_codes"], [1, 2])
        self.assertEqual(result["query"]["chart_mode"], "periodcompare")
        self.assertEqual(mocked.call_count, 2)

    @patch("agent_query_router._matrix", side_effect=matrix)
    def test_one_class_across_intervals_still_works(self, _):
        result = _compare_explicit_outgoing_periods(
            "2023到2025年和2016到2025年耕地转出哪个多")
        self.assertEqual([r["class_code"] for r in result["facts"]], [1, 1])

    @patch("agent_query_router._matrix", side_effect=matrix)
    def test_ambiguous_pairing_is_rejected_before_data_access(self, mocked):
        with self.assertRaises(HTTPException) as caught:
            _compare_explicit_outgoing_periods(
                "耕地和林地在2023到2025年与2016到2025年哪个转出多")
        self.assertEqual(caught.exception.status_code, 400)
        mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
