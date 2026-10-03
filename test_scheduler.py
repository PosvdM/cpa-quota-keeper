import unittest
from datetime import datetime, timedelta, timezone

import scheduler
import watcher


class SchedulerTests(unittest.TestCase):
    TZ = timezone(timedelta(hours=8))

    def due(self, local_now, reset):
        account = {"id": "codex:test", "provider": "codex", "label": "Test", "reset": reset}
        return scheduler.due_at(account, {}, local_now.astimezone(timezone.utc)).astimezone(self.TZ)

    def test_trigger_prompt_is_strict_and_unified(self):
        self.assertIn("Do not think, reason, deliberate", scheduler.TRIGGER_PROMPT)
        self.assertIn("Reply with exactly OK and nothing else.", scheduler.TRIGGER_PROMPT)

    def test_waits_for_daily_start(self):
        now = datetime(2026, 9, 28, 6, 59, tzinfo=self.TZ)
        due = self.due(now, "2026-09-27T20:00:00+00:00")
        self.assertEqual(due.strftime("%Y-%m-%d %H:%M:%S"), "2026-09-28 07:00:00")

    def test_uses_real_reset_plus_grace(self):
        now = datetime(2026, 9, 28, 9, 0, tzinfo=self.TZ)
        due = self.due(now, "2026-09-28T04:00:00+00:00")
        self.assertEqual(due.strftime("%Y-%m-%d %H:%M:%S"), "2026-09-28 12:00:03")

    def test_rolling_reset_is_detected_from_two_observations(self):
        state = {}
        account = {
            "id": "codex:test",
            "provider": "codex",
            "label": "ChatGPT",
            "remaining": 100.0,
            "reset": "2026-09-28T06:00:00+00:00",
        }
        first = datetime(2026, 9, 28, 1, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(scheduler.observe_reset_behavior(state, account, first))
        account["reset"] = "2026-09-28T06:05:00+00:00"
        second = first + timedelta(minutes=5)
        self.assertTrue(scheduler.observe_reset_behavior(state, account, second))
        due = scheduler.due_at(account, state, second)
        self.assertEqual(due, second)

    def test_fixed_reset_at_100_percent_is_not_treated_as_unstarted(self):
        state = {}
        account = {
            "id": "claude:test",
            "provider": "claude",
            "label": "Claude",
            "remaining": 100.0,
            "reset": "2026-09-28T06:00:00+00:00",
        }
        first = datetime(2026, 9, 28, 1, 0, 0, tzinfo=timezone.utc)
        scheduler.observe_reset_behavior(state, account, first)
        second = first + timedelta(seconds=10)
        self.assertFalse(scheduler.observe_reset_behavior(state, account, second))
        due = scheduler.due_at(account, state, second)
        self.assertEqual(due, datetime(2026, 9, 28, 6, 0, 3, tzinfo=timezone.utc))

    def test_rolling_reset_does_not_invent_five_hour_wait(self):
        state = {
            "scheduler": {
                "antigravity:test": {
                    "rolling_reset": True,
                    "last_success_epoch": datetime(2026, 9, 28, 5, 0, 0, tzinfo=timezone.utc).timestamp(),
                }
            }
        }
        account = {
            "id": "antigravity:test",
            "provider": "antigravity",
            "label": "Gemini",
            "remaining": 100.0,
            "reset": "2026-09-28T11:01:00+00:00",
        }
        now = datetime(2026, 9, 28, 6, 1, 0, tzinfo=timezone.utc)
        self.assertEqual(scheduler.due_at(account, state, now), now)

    def test_rolling_reset_detection_does_not_depend_on_quota_percent(self):
        state = {}
        account = {
            "id": "test:not-full",
            "provider": "codex",
            "label": "Test",
            "remaining": 99.0,
            "reset": "2026-09-28T06:00:00+00:00",
        }
        first = datetime(2026, 9, 28, 1, 0, 0, tzinfo=timezone.utc)
        scheduler.observe_reset_behavior(state, account, first)
        account["reset"] = "2026-09-28T06:05:00+00:00"
        self.assertTrue(scheduler.observe_reset_behavior(state, account, first + timedelta(minutes=5)))

    def test_skips_overnight_reset(self):
        now = datetime(2026, 9, 28, 22, 0, tzinfo=self.TZ)
        due = self.due(now, "2026-09-28T19:00:00+00:00")
        self.assertEqual(due.strftime("%Y-%m-%d %H:%M:%S"), "2026-09-29 07:00:00")

    def test_after_cutoff_waits_for_next_day(self):
        now = datetime(2026, 9, 28, 22, 31, tzinfo=self.TZ)
        due = self.due(now, "2026-09-28T14:00:00+00:00")
        self.assertEqual(due.strftime("%Y-%m-%d %H:%M:%S"), "2026-09-29 07:00:00")

    def test_codex_ignition_uses_cpa_executor_bridge_and_exact_auth(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def management(self, path, method="GET", data=None):
                self.calls.append((path, method, data))
                return {"ok": True}

        client = FakeClient()
        account = {"provider": "codex", "file": {"provider": "codex", "auth_index": "auth-A"}}
        scheduler.ignite_codex(client, account, "gpt-6-luna")
        path, method, data = client.calls[0]
        self.assertEqual((path, method), (scheduler.CPA_IGNITE_PATH, "POST"))
        self.assertEqual(data["auth_index"], "auth-A")
        self.assertEqual(data["model"], "gpt-6-luna")
        self.assertEqual(data["entry_protocol"], "openai-response")
        self.assertEqual(data["body"]["reasoning"], {"effort": "none"})
        self.assertEqual(data["body"]["tools"], [])



    def test_compact_duration_uses_letter_units(self):
        self.assertEqual(watcher.format_compact_duration(6 * 86400), "06d")
        self.assertEqual(watcher.format_compact_duration(5 * 3600), "05h")
        self.assertEqual(watcher.format_compact_duration(30 * 60), "30m")

    def test_codex_reset_scheduled_notification(self):
        record = {
            "id": "schedule-1",
            "kind": "reset_scheduled",
            "resetType": "global",
            "effectiveAt": "2026-10-02T18:00:00+00:00",
            "confidence": 0.95,
            "scope": {"plans": ["all"], "windows": ["unknown"]},
            "source": {"handle": "thsottiaux"},
        }
        title, body, level = scheduler.build_codex_reset_notification(record)
        self.assertEqual(title, "📅 Codex 全局重置已排期")
        self.assertIn("预计：10/03 02:00", body)
        self.assertIn("置信度：95%", body)
        self.assertIn("范围：全部套餐", body)
        self.assertNotIn("来源：", body)
        self.assertEqual(level, "active")
        self.assertEqual(
            scheduler.codex_reset_detail_url({"announcedAt": "2026-10-02T02:14:51.000Z"}),
            "https://didcodexreset.com/zh/history/1790907291000.html",
        )
        self.assertIsNone(scheduler.codex_reset_detail_url({"announcedAt": None}))

    def test_codex_reset_first_run_only_notifies_current_pending(self):
        records = [
            {
                "id": "schedule-1",
                "kind": "reset_scheduled",
                "resetType": "global",
                "scheduleState": "pending",
                "effectiveAt": "2026-10-02T18:00:00+00:00",
            },
            {
                "id": "old-completed",
                "kind": "reset_completed",
                "resetType": "global",
                "effectiveAt": "2026-09-30T18:00:00+00:00",
            },
        ]
        state = {}
        old_send = watcher.send_bark
        sent = []
        try:
            watcher.send_bark = lambda title, body, level, jump_url=None: sent.append((title, body, level, jump_url)) or True
            count = scheduler.process_codex_reset_records(state, records)
        finally:
            watcher.send_bark = old_send
        self.assertEqual(count, 1)
        self.assertEqual(len(sent), 1)
        self.assertIn("已排期", sent[0][0])
        self.assertIsNone(sent[0][3])
        self.assertIn("schedule-1", state["codex_reset_updates"]["seen_ids"])
        self.assertIn("old-completed", state["codex_reset_updates"]["seen_ids"])

    def test_codex_reset_notification_passes_detail_url_to_bark(self):
        state = {
            "codex_reset_updates": {
                "initialized": True,
                "stable_key_migrated": True,
                "seen_keys": [],
            }
        }
        record = {
            "id": "new-scheduled",
            "kind": "reset_scheduled",
            "resetType": "global",
            "announcedAt": "2026-10-02T02:14:51.000Z",
            "effectiveAt": "2026-10-02T18:00:00.000Z",
            "scheduleState": "pending",
        }
        old_send = watcher.send_bark
        sent = []
        try:
            watcher.send_bark = lambda title, body, level, jump_url=None: sent.append((title, body, level, jump_url)) or True
            count = scheduler.process_codex_reset_records(state, [record])
        finally:
            watcher.send_bark = old_send
        self.assertEqual(count, 1)
        self.assertEqual(
            sent[0][3],
            "https://didcodexreset.com/zh/history/1790907291000.html",
        )

    def test_codex_reset_manual_record_id_rotation_does_not_duplicate(self):
        state = {
            "codex_reset_updates": {
                "initialized": True,
                "stable_key_migrated": True,
                "seen_keys": [],
            }
        }
        first_record = {
            "id": "manual:cl_first",
            "kind": "reset_completed",
            "resetType": "banked",
            "effectiveAt": "2026-09-22T20:37:00.000Z",
            "completedAt": "2026-09-22T20:37:00.000Z",
            "scope": {"plans": ["all"]},
        }
        second_record = dict(first_record, id="manual:cl_second")
        old_send = watcher.send_bark
        sent = []
        try:
            watcher.send_bark = lambda title, body, level, jump_url=None: sent.append((title, body, level, jump_url)) or True
            first = scheduler.process_codex_reset_records(state, [first_record])
            second = scheduler.process_codex_reset_records(state, [second_record])
        finally:
            watcher.send_bark = old_send
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(len(sent), 1)

    def test_codex_reset_id_only_state_migrates_manual_history_without_push(self):
        state = {
            "codex_reset_updates": {
                "initialized": True,
                "seen_ids": ["manual:cl_old"],
            }
        }
        record = {
            "id": "manual:cl_rotated",
            "kind": "reset_completed",
            "resetType": "banked",
            "effectiveAt": "2026-09-22T20:37:00.000Z",
            "completedAt": "2026-09-22T20:37:00.000Z",
            "scope": {"plans": ["all"]},
        }
        old_send = watcher.send_bark
        sent = []
        try:
            watcher.send_bark = lambda title, body, level, jump_url=None: sent.append((title, body, level, jump_url)) or True
            count = scheduler.process_codex_reset_records(state, [record])
        finally:
            watcher.send_bark = old_send
        self.assertEqual(count, 0)
        self.assertEqual(sent, [])
        self.assertTrue(state["codex_reset_updates"]["stable_key_migrated"])

    def test_codex_reset_new_record_is_deduplicated(self):
        state = {
            "codex_reset_updates": {
                "initialized": True,
                "seen_ids": ["old-completed"],
            }
        }
        records = [
            {"id": "new-completed", "kind": "reset_completed", "resetType": "banked"},
            {"id": "old-completed", "kind": "reset_completed", "resetType": "global"},
        ]
        old_send = watcher.send_bark
        sent = []
        try:
            watcher.send_bark = lambda title, body, level, jump_url=None: sent.append((title, body, level, jump_url)) or True
            first = scheduler.process_codex_reset_records(state, records)
            second = scheduler.process_codex_reset_records(state, records)
        finally:
            watcher.send_bark = old_send
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(len(sent), 1)
        self.assertIn("重置卡已到账", sent[0][0])

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

    def test_antigravity_claude_gpt_prefers_non_thinking_claude_over_gpt_oss(self):
        account = {
            "provider": "antigravity",
            "group_label": "Claude / GPT",
            "file": {"provider": "antigravity", "auth_index": "auth-AG"},
        }
        old_models_for = scheduler.models_for
        try:
            scheduler.models_for = lambda client, file: [
                "claude-opus-4-6-thinking",
                "claude-sonnet-4-6",
                "gpt-oss-120b-medium",
            ]
            selected = scheduler.choose_model(object(), account)
        finally:
            scheduler.models_for = old_models_for
        self.assertEqual(selected, "claude-sonnet-4-6")

    def test_antigravity_claude_gpt_keeps_gpt_oss_as_non_thinking_fallback(self):
        account = {
            "provider": "antigravity",
            "group_label": "Claude / GPT",
            "file": {"provider": "antigravity", "auth_index": "auth-AG"},
        }
        old_models_for = scheduler.models_for
        try:
            scheduler.models_for = lambda client, file: [
                "claude-opus-4-6-thinking",
                "gpt-oss-120b-medium",
            ]
            selected = scheduler.choose_model(object(), account)
        finally:
            scheduler.models_for = old_models_for
        self.assertEqual(selected, "gpt-oss-120b-medium")

    def test_codex_uses_newest_luna_then_older_luna(self):
        account = {
            "provider": "codex",
            "file": {"provider": "codex", "auth_index": "auth-CX"},
        }
        old_models_for = scheduler.models_for
        try:
            scheduler.models_for = lambda client, file: [
                "gpt-5.6-luna",
                "gpt-6-luna",
                "gpt-6.1-luna",
                "gpt-6.2-sol",
            ]
            self.assertEqual(scheduler.choose_model(object(), account), "gpt-6.1-luna")
            scheduler.models_for = lambda client, file: ["gpt-5.6-luna", "gpt-6-luna"]
            self.assertEqual(scheduler.choose_model(object(), account), "gpt-6-luna")
        finally:
            scheduler.models_for = old_models_for

    def test_claude_uses_newest_haiku_then_older_haiku(self):
        account = {
            "provider": "claude",
            "file": {"provider": "claude", "auth_index": "auth-CL"},
        }
        old_models_for = scheduler.models_for
        try:
            scheduler.models_for = lambda client, file: [
                "claude-3-5-haiku-20241022",
                "claude-haiku-4-5-20251001",
                "claude-haiku-5-20261001",
                "claude-sonnet-5-5",
            ]
            self.assertEqual(scheduler.choose_model(object(), account), "claude-haiku-5-20261001")
            scheduler.models_for = lambda client, file: [
                "claude-3-5-haiku-20241022",
                "claude-haiku-4-5-20251001",
            ]
            self.assertEqual(scheduler.choose_model(object(), account), "claude-haiku-4-5-20251001")
        finally:
            scheduler.models_for = old_models_for

    def test_antigravity_gemini_uses_newest_flash_then_older_flash(self):
        account = {
            "provider": "antigravity",
            "group_label": "Gemini",
            "file": {"provider": "antigravity", "auth_index": "auth-AG"},
        }
        old_models_for = scheduler.models_for
        try:
            scheduler.models_for = lambda client, file: [
                "gemini-3.5-flash-lite",
                "gemini-3.6-flash-high",
                "gemini-3.8-flash-high",
                "gemini-3.7-flash-high",
                "gemini-4-pro",
                "gemini-3.9-flash-image",
            ]
            self.assertEqual(scheduler.choose_model(object(), account), "gemini-3.8-flash-high")
            scheduler.models_for = lambda client, file: [
                "gemini-3.5-flash-lite",
                "gemini-3.7-flash-high",
            ]
            self.assertEqual(scheduler.choose_model(object(), account), "gemini-3.7-flash-high")
            scheduler.models_for = lambda client, file: [
                "gemini-pro-agent",
                "gemini-3.1-pro-low",
            ]
            self.assertEqual(scheduler.choose_model(object(), account), "gemini-3.1-pro-low")
        finally:
            scheduler.models_for = old_models_for

    def test_antigravity_claude_gpt_auto_ignition_is_off_by_default(self):
        old = scheduler.PROVIDERS_CONFIG
        try:
            scheduler.PROVIDERS_CONFIG = {"antigravity": {"monitor": True, "ignite": True}}
            group = {"source_label": "Claude / GPT", "label": "Claude / GPT"}
            self.assertFalse(scheduler.group_ignition_enabled("antigravity", group))
        finally:
            scheduler.PROVIDERS_CONFIG = old

    def test_claude_fable_5_window_is_monitor_only(self):
        group = {
            "label": "Claude",
            "windows": [
                {
                    "id": "seven-day-fable-5",
                    "label": "7 Day Fable 5",
                    "period_hours": 168,
                    "remaining": 100.0,
                }
            ],
        }
        self.assertIsNone(scheduler.five_hour_window(group))

    def test_antigravity_ignite_uses_cpa_executor_bridge_and_exact_auth(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def management(self, path, method="GET", data=None):
                self.calls.append((path, method, data))
                return {"ok": True}

        client = FakeClient()
        account = {
            "provider": "antigravity",
            "file": {"provider": "antigravity", "auth_index": "auth-AG"},
        }
        scheduler.ignite_antigravity(client, account, "gemini-demo")
        path, method, data = client.calls[0]
        self.assertEqual((path, method), (scheduler.CPA_IGNITE_PATH, "POST"))
        self.assertEqual(data["auth_index"], "auth-AG")
        self.assertEqual(data["model"], "gemini-demo")
        self.assertEqual(data["entry_protocol"], "openai")
        self.assertEqual(data["body"]["reasoning_effort"], "none")



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

    def test_xai_ignite_uses_cpa_executor_bridge_and_exact_auth(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def management(self, path, method="GET", data=None):
                self.calls.append((path, method, data))
                return {"ok": True}

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
        path, method, data = client.calls[0]
        self.assertEqual((path, method), (scheduler.CPA_IGNITE_PATH, "POST"))
        self.assertEqual(data["auth_index"], "auth-X")
        self.assertEqual(data["model"], "grok-demo")
        self.assertEqual(data["entry_protocol"], "openai")

    def test_hard_ignition_failure_opens_circuit_immediately(self):
        item = {}
        account = {"label": "Gemini"}
        now = datetime(2026, 10, 3, 11, 0, tzinfo=self.TZ).astimezone(timezone.utc)
        old_send = watcher.send_bark
        sent = []
        try:
            watcher.send_bark = lambda title, body, level: sent.append((title, body, level)) or True
            scheduler.record_ignition_failure(item, account, now, RuntimeError("HTTP 404: NOT_FOUND"))
        finally:
            watcher.send_bark = old_send
        self.assertEqual(item["consecutive_failures"], 1)
        self.assertGreater(item["circuit_open_until_epoch"], now.timestamp())
        self.assertEqual(item["retry_at_epoch"], item["circuit_open_until_epoch"])
        self.assertEqual(len(sent), 1)

    def test_transient_ignition_failure_backs_off_then_circuits(self):
        item = {}
        account = {"label": "Gemini"}
        now = datetime(2026, 10, 3, 11, 0, tzinfo=self.TZ).astimezone(timezone.utc)
        old_send = watcher.send_bark
        sent = []
        try:
            watcher.send_bark = lambda title, body, level: sent.append((title, body, level)) or True
            scheduler.record_ignition_failure(item, account, now, RuntimeError("temporary network error"))
            first_retry = item["retry_at_epoch"]
            scheduler.record_ignition_failure(item, account, now + timedelta(minutes=5), RuntimeError("temporary network error"))
            second_retry = item["retry_at_epoch"]
            scheduler.record_ignition_failure(item, account, now + timedelta(minutes=20), RuntimeError("temporary network error"))
        finally:
            watcher.send_bark = old_send
        self.assertEqual(first_retry, now.timestamp() + scheduler.IGNITE_FAILURE_RETRY_SECONDS)
        self.assertEqual(
            second_retry,
            (now + timedelta(minutes=5)).timestamp()
            + scheduler.IGNITE_FAILURE_RETRY_SECONDS * scheduler.IGNITE_FAILURE_BACKOFF_MULTIPLIER,
        )
        self.assertIn("circuit_open_until_epoch", item)
        self.assertEqual(len(sent), 1)



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

