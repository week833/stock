"""Small checks for terminal progress reporting and command wiring."""
import io
import sys
import threading
import unittest
from unittest.mock import patch

import app
from progress import progress_session, stage


class _EventStream(io.StringIO):
    def __init__(self):
        super().__init__()
        self.wrote = threading.Event()

    def write(self, value):
        result = super().write(value)
        self.wrote.set()
        return result


class ProgressChecks(unittest.TestCase):
    def test_known_total_shows_real_count_and_percentage(self):
        output = _EventStream()
        with progress_session(stream=output, interval=0.01):
            with stage("讀取資料", total=4, unit="筆") as task:
                output.wrote.clear()
                task.update(2)
                self.assertTrue(output.wrote.wait(1.0), "heartbeat did not render")
        lines = output.getvalue().splitlines()
        self.assertTrue(any("2/4筆 50%" in line for line in lines), lines)
        self.assertIn("完成 讀取資料", lines[-1])

    def test_unknown_total_has_no_percent_or_running_on_completion(self):
        output = io.StringIO()
        with progress_session(stream=output, interval=60):
            with stage("建立特徵") as task:
                task.advance(5)
        lines = output.getvalue().splitlines()
        self.assertIn("執行中", lines[0])
        self.assertIn("完成 建立特徵", lines[-1])
        self.assertNotIn("執行中", lines[-1])
        self.assertNotIn("%", lines[-1])

    def test_failed_stage_never_reports_100_percent(self):
        output = io.StringIO()
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            with progress_session(stream=output, interval=60):
                with stage("建立模型", total=5) as task:
                    task.update(5)
                    raise RuntimeError("fixture failure")
        final_line = output.getvalue().splitlines()[-1]
        self.assertIn("失敗 建立模型", final_line)
        self.assertNotIn("100%", final_line)

    def test_disabled_session_is_silent(self):
        output = io.StringIO()
        with progress_session(enabled=False, stream=output):
            with stage("停用階段", total=1) as task:
                task.advance()
        self.assertEqual(output.getvalue(), "")

    def test_nested_stage_restores_parent(self):
        output = io.StringIO()
        with progress_session(stream=output, interval=60):
            with stage("父階段"):
                with stage("子階段"):
                    pass
        lines = output.getvalue().splitlines()
        child_done = next(i for i, line in enumerate(lines) if "完成 子階段" in line)
        parent_restored = next(i for i, line in enumerate(lines) if i > child_done and "執行中 父階段" in line)
        parent_done = next(i for i, line in enumerate(lines) if "完成 父階段" in line)
        self.assertLess(child_done, parent_restored)
        self.assertLess(parent_restored, parent_done)

    def test_main_wraps_mocked_predict_in_progress_session(self):
        output = io.StringIO()

        def mocked_predict(**kwargs):
            with stage("測試接線", total=1):
                pass

        with patch.object(sys, "argv", ["app.py", "predict"]), \
             patch.object(app, "predict", side_effect=mocked_predict):
            with patch("sys.stderr", output):
                app.main()
        self.assertIn("完成 測試接線", output.getvalue())


if __name__ == "__main__":
    unittest.main()
