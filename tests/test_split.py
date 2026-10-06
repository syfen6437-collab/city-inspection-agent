from __future__ import annotations

import unittest

from city_agent.pipeline import _matches_split


class SplitTests(unittest.TestCase):
    def test_split_detection(self) -> None:
        self.assertTrue(_matches_split("根/初赛测试集/a.docx", "test"))
        self.assertTrue(_matches_split("根/赛题一_训练集/2018年/a.docx", "train"))
        self.assertFalse(_matches_split("根/赛题一_训练集标签/2018年/a.docx", "train"))
        self.assertTrue(_matches_split("根/赛题一_训练集标签/2018年/a.docx", "labels"))


if __name__ == "__main__":
    unittest.main()
