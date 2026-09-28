import unittest
from datetime import datetime, timedelta, timezone

import scheduler


class SchedulerTests(unittest.TestCase):
    TZ = timezone(timedelta(hours=8))

    def due(self, local_now, reset):
        account = {"id": "codex:test", "provider": "codex", "label": "Test", "reset": reset}
        return scheduler.due_at(account, {}, local_now.astimezone(timezone.utc)).astimezone(self.TZ)

    def test_waits_for_daily_start(self):
        now = datetime(2026, 9, 28, 6, 59, tzinfo=self.TZ)
        due = self.due(now, "2026-09-27T20:00:00+00:00")
        self.assertEqual(due.strftime("%Y-%m-%d %H:%M:%S"), "2026-09-28 07:00:00")

    def test_uses_real_reset_plus_grace(self):
        now = datetime(2026, 9, 28, 9, 0, tzinfo=self.TZ)
        due = self.due(now, "2026-09-28T04:00:00+00:00")
        self.assertEqual(due.strftime("%Y-%m-%d %H:%M:%S"), "2026-09-28 12:00:03")

    def test_skips_overnight_reset(self):
        now = datetime(2026, 9, 28, 22, 0, tzinfo=self.TZ)
        due = self.due(now, "2026-09-28T19:00:00+00:00")
        self.assertEqual(due.strftime("%Y-%m-%d %H:%M:%S"), "2026-09-29 07:00:00")

    def test_after_cutoff_waits_for_next_day(self):
        now = datetime(2026, 9, 28, 22, 31, tzinfo=self.TZ)
        due = self.due(now, "2026-09-28T14:00:00+00:00")
        self.assertEqual(due.strftime("%Y-%m-%d %H:%M:%S"), "2026-09-29 07:00:00")

    def test_codex_direct_call_uses_only_selected_auth(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def api_call(self, auth_index, method, url, headers, data=None):
                self.calls.append((auth_index, method, url))
                raise RuntimeError("forced failure")

        client = FakeClient()
        account = {"provider": "codex", "file": {"provider": "codex", "auth_index": "auth-A"}}
        with self.assertRaises(RuntimeError):
            scheduler.ignite_codex(client, account, "gpt-6-luna")
        self.assertEqual(
            client.calls,
            [("auth-A", "POST", scheduler.CODEX_RESPONSES_URL)],
        )


if __name__ == "__main__":
    unittest.main()
