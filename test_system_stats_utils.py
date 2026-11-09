import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from system_stats_utils import (  # noqa: E402
    cpu_usage_percent,
    graph_points,
    parse_cpu_times,
    parse_temperature_celsius,
    summarize,
)


class CpuTests(unittest.TestCase):
    def test_parse_cpu_times_excludes_idle_and_iowait(self):
        text = "cpu  100 0 50 800 40 5 5 0 0 0\ncpu0 100 0 50 800 40 5 5 0 0 0\nintr 1 2 3\n"
        busy, total = parse_cpu_times(text)
        self.assertEqual(total, 1000)
        self.assertEqual(busy, 160)  # 1000 - idle(800) - iowait(40)

    def test_parse_cpu_times_rejects_garbage(self):
        self.assertIsNone(parse_cpu_times(""))
        self.assertIsNone(parse_cpu_times("cpu  a b c d\n"))
        self.assertIsNone(parse_cpu_times("cpu  1 2\n"))
        self.assertIsNone(parse_cpu_times("meminfo\n"))

    def test_usage_percent(self):
        self.assertAlmostEqual(cpu_usage_percent((0, 0), (25, 100)), 25.0)
        self.assertAlmostEqual(cpu_usage_percent((100, 400), (100, 500)), 0.0)

    def test_usage_percent_invalid(self):
        self.assertIsNone(cpu_usage_percent(None, (1, 2)))
        self.assertIsNone(cpu_usage_percent((1, 2), None))
        self.assertIsNone(cpu_usage_percent((10, 100), (10, 100)))   # no time passed
        self.assertIsNone(cpu_usage_percent((50, 100), (40, 200)))   # counters went backwards


class TemperatureTests(unittest.TestCase):
    def test_millidegrees(self):
        self.assertAlmostEqual(parse_temperature_celsius("48312\n"), 48.312)

    def test_plain_degrees_and_invalid(self):
        self.assertEqual(parse_temperature_celsius("48"), 48.0)
        self.assertIsNone(parse_temperature_celsius("abc"))
        self.assertIsNone(parse_temperature_celsius(""))
        self.assertIsNone(parse_temperature_celsius("999999"))   # 999 degC is not real


class SummaryTests(unittest.TestCase):
    def test_summarize(self):
        result = summarize([10.0, None, 30.0, 20.0])
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["current"], 20.0)
        self.assertEqual(result["peak"], 30.0)
        self.assertAlmostEqual(result["average"], 20.0)

    def test_summarize_empty(self):
        self.assertIsNone(summarize([]))
        self.assertIsNone(summarize([None, None]))


class GraphTests(unittest.TestCase):
    BOX = (10, 50, 250, 110)   # 240 wide, 60 tall

    def test_newest_value_is_at_the_right_edge(self):
        points = graph_points([0.0, 100.0], self.BOX, slots=60)
        self.assertEqual(points[-1], (250, 50))     # 100% -> top, right edge
        self.assertEqual(points[0][1], 110)         # 0% -> bottom
        self.assertLess(points[0][0], points[1][0])

    def test_full_history_spans_the_box(self):
        points = graph_points([50.0] * 60, self.BOX, slots=60)
        self.assertEqual(points[0], (10, 80))
        self.assertEqual(points[-1], (250, 80))
        self.assertEqual(len(points), 60)

    def test_values_are_clamped_and_extra_history_is_trimmed(self):
        points = graph_points([-20.0, 500.0] + [0.0] * 60, self.BOX, slots=60)
        self.assertEqual(len(points), 60)
        self.assertTrue(all(50 <= y <= 110 for _, y in points))
        clamped = graph_points([500.0], self.BOX, slots=60)
        self.assertEqual(clamped[0][1], 50)

    def test_empty(self):
        self.assertEqual(graph_points([], self.BOX), [])


if __name__ == "__main__":
    unittest.main()
