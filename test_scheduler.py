import unittest
from datetime import datetime, timedelta, timezone

import scheduler
import watcher


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


    def test_compact_duration_uses_letter_units(self):
        self.assertEqual(watcher.format_compact_duration(6 * 86400), "06d")
        self.assertEqual(watcher.format_compact_duration(5 * 3600), "05h")
        self.assertEqual(watcher.format_compact_duration(30 * 60), "30m")

    def test_notification_title_has_no_cpa_prefix(self):
        group = {
            "label": "Claude",
            "windows": [
                {"id": "seven-day", "label": "7 Day", "remaining": 48.0, "reset": None},
            ],
        }
        changes = [
            {
                "id": "seven-day",
                "label": "7 Day",
                "from": "normal",
                "to": "notice",
                "direction": "down",
                "remaining": 48.0,
            }
        ]
        title, _, _ = watcher.build_notification(group, changes)
        self.assertEqual(title, "⚠️ Claude · 7d 48%")
        self.assertNotIn("CPA", title)

    def test_notification_title_includes_approximate_reset(self):
        reset = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        group = {
            "label": "ChatGPT#eg",
            "windows": [
                {"id": "five-hour", "label": "5 小时", "remaining": 26.0, "reset": reset},
            ],
        }
        changes = [
            {
                "id": "five-hour",
                "label": "5 小时",
                "from": "normal",
                "to": "notice",
                "direction": "down",
                "remaining": 26.0,
            }
        ]
        title, _, _ = watcher.build_notification(group, changes)
        self.assertEqual(title, "⚠️ ChatGPT#eg · 5h 26% | 05h")

    def test_chatgpt_account_labels_are_generated_from_email(self):
        a = {
            "provider": "codex",
            "auth_index": "auth-A",
            "email": "alice.work@example.com",
            "name": "codex-33a4fef5-alice.work@example.com-plus.json",
        }
        b = {
            "provider": "codex",
            "auth_index": "auth-B",
            "email": "bob.team@example.net",
            "name": "codex-7b0a37c4-bob.team@example.net-team.json",
        }
        self.assertEqual(scheduler.credential_label(a, 1, 2), "ChatGPT#rk")
        self.assertEqual(scheduler.credential_label(b, 2, 2), "ChatGPT#am")

    def test_single_account_provider_has_no_suffix(self):
        a = {
            "provider": "claude",
            "auth_index": "auth-C",
            "email": "single.user@example.org",
        }
        self.assertEqual(scheduler.credential_label(a, 1, 1), "Claude")

    def test_legacy_state_is_merged_before_processing(self):
        state = {
            "groups": {
                "claude:main": {
                    "windows": {
                        "seven-day": {
                            "severity": "notice",
                            "notified_severity": "notice",
                            "remaining": 48.0,
                            "reset": "2026-10-01T06:00:00+00:00",
                        }
                    }
                },
                "claude:auth-A:claude:main": {
                    "windows": {
                        "seven-day": {
                            "severity": "notice",
                            "remaining": 48.0,
                            "reset": "2026-10-01T05:59:59+00:00",
                        }
                    }
                },
            }
        }
        group = {
            "key": "claude:auth-A:claude:main",
            "legacy_key": "claude:main",
            "label": "Claude",
            "windows": [],
        }
        old_process = watcher.process_group
        old_save = watcher.save_state
        try:
            watcher.process_group = lambda state, group: None
            watcher.save_state = lambda state: None
            scheduler.process_groups(state, [group])
        finally:
            watcher.process_group = old_process
            watcher.save_state = old_save

        self.assertNotIn("claude:main", state["groups"])
        merged = state["groups"]["claude:auth-A:claude:main"]["windows"]["seven-day"]
        self.assertEqual(merged["notified_severity"], "notice")


    def test_antigravity_can_be_enabled_by_provider_config(self):
        old = scheduler.PROVIDERS_CONFIG
        try:
            scheduler.PROVIDERS_CONFIG = {"antigravity": {"monitor": True, "ignite": True}}
            group = {"source_label": "Gemini", "label": "Gemini"}
            self.assertTrue(scheduler.group_ignition_enabled("antigravity", group))
        finally:
            scheduler.PROVIDERS_CONFIG = old

    def test_antigravity_ignite_uses_exact_auth(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def api_call(self, auth_index, method, url, headers, data=None):
                self.calls.append((auth_index, method, url, data))
                return '{"response":{"candidates":[{"content":{"parts":[{"text":"OK"}]}}]}}'

        client = FakeClient()
        account = {
            "provider": "antigravity",
            "file": {"provider": "antigravity", "auth_index": "auth-AG"},
        }
        old_resolve = watcher.resolve_project_id
        try:
            watcher.resolve_project_id = lambda client, file: "project-demo"
            scheduler.ignite_antigravity(client, account, "gemini-demo")
        finally:
            watcher.resolve_project_id = old_resolve

        self.assertEqual(client.calls[0][0:3], ("auth-AG", "POST", scheduler.ANTIGRAVITY_GENERATE_URL))
        self.assertIn('"project":"project-demo"', client.calls[0][3])

    def test_xai_five_hour_billing_is_normalized(self):
        class FakeClient:
            def api_call(self, auth_index, method, url, headers, data=None):
                return """{
                  "config": {
                    "currentPeriod": {
                      "start": "2026-09-28T00:00:00Z",
                      "end": "2026-09-28T05:00:00Z"
                    },
                    "creditUsagePercent": 25
                  }
                }"""

        groups = scheduler.fetch_xai_groups(
            FakeClient(),
            {"provider": "xai", "auth_index": "auth-X"},
        )
        window = groups[0]["windows"][0]
        self.assertEqual(window["label"], "5 小时")
        self.assertEqual(window["remaining"], 75.0)
        self.assertTrue(scheduler.is_five_hour_window(window))

    def test_xai_ignite_uses_exact_auth(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def api_call(self, auth_index, method, url, headers, data=None):
                self.calls.append((auth_index, method, url, data))
                return '{"id":"resp_demo","output":[{"type":"message"}]}'

        client = FakeClient()
        account = {
            "provider": "xai",
            "file": {
                "provider": "xai",
                "auth_index": "auth-X",
                "auth_kind": "oauth",
            },
        }
        scheduler.ignite_xai(client, account, "grok-demo")
        self.assertEqual(client.calls[0][0:3], ("auth-X", "POST", scheduler.XAI_CLI_RESPONSES_URL))


    def test_reset_reminder_can_be_disabled(self):
        state = {"groups": {}}
        group = {
            "key": "test:reset-disabled",
            "label": "Test",
            "windows": [
                {
                    "id": "five-hour",
                    "label": "5 小时",
                    "remaining": 100.0,
                    "reset": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
                }
            ],
        }
        old_flag = watcher.NOTIFY_RESET_REMINDERS
        old_send = watcher.send_bark
        sent = []
        try:
            watcher.NOTIFY_RESET_REMINDERS = False
            watcher.send_bark = lambda title, body, level: sent.append((title, body, level)) or True
            watcher.process_group(state, group)
        finally:
            watcher.NOTIFY_RESET_REMINDERS = old_flag
            watcher.send_bark = old_send
        self.assertEqual(sent, [])


    def test_low_quota_alert_survives_quiet_reset_settings(self):
        state = {"groups": {}}
        group = {
            "key": "test:low-quota",
            "label": "Test",
            "windows": [
                {"id": "five-hour", "label": "5 小时", "remaining": 20.0, "reset": None}
            ],
        }
        old_recovery = watcher.NOTIFY_RECOVERY
        old_reset = watcher.NOTIFY_RESET_REMINDERS
        old_send = watcher.send_bark
        sent = []
        try:
            watcher.NOTIFY_RECOVERY = False
            watcher.NOTIFY_RESET_REMINDERS = False
            watcher.send_bark = lambda title, body, level: sent.append((title, body, level)) or True
            watcher.process_group(state, group)
        finally:
            watcher.NOTIFY_RECOVERY = old_recovery
            watcher.NOTIFY_RESET_REMINDERS = old_reset
            watcher.send_bark = old_send
        self.assertEqual(len(sent), 1)
        self.assertIn("5h 20%", sent[0][0])

    def test_quiet_recovery_resets_notification_baseline(self):
        state = {
            "groups": {
                "test:quiet-recovery": {
                    "windows": {
                        "five-hour": {
                            "severity": "exhausted",
                            "notified_severity": "exhausted",
                            "remaining": 0.0,
                            "reset": None,
                        }
                    }
                }
            }
        }
        group = {
            "key": "test:quiet-recovery",
            "label": "Test",
            "windows": [
                {"id": "five-hour", "label": "5 小时", "remaining": 100.0, "reset": None}
            ],
        }
        old_recovery = watcher.NOTIFY_RECOVERY
        old_send = watcher.send_bark
        sent = []
        try:
            watcher.NOTIFY_RECOVERY = False
            watcher.send_bark = lambda title, body, level: sent.append((title, body, level)) or True

            watcher.process_group(state, group)
            self.assertEqual(sent, [])
            window_state = state["groups"]["test:quiet-recovery"]["windows"]["five-hour"]
            self.assertEqual(window_state["notified_severity"], "normal")

            group["windows"][0]["remaining"] = 49.0
            watcher.process_group(state, group)
        finally:
            watcher.NOTIFY_RECOVERY = old_recovery
            watcher.send_bark = old_send

        self.assertEqual(len(sent), 1)
        self.assertIn("5h 49%", sent[0][0])
        window_state = state["groups"]["test:quiet-recovery"]["windows"]["five-hour"]
        self.assertEqual(window_state["notified_severity"], "notice")


if __name__ == "__main__":
    unittest.main()

