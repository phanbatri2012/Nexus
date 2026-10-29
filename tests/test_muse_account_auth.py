import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus_engine import main
from nexus_engine.services import muse_account_auth, muse_provider
from nexus_engine.services.secret_store import SecretStorageError


def _fake_encrypt(value: str) -> str:
    return f"dpapi:{value[::-1]}"


def _fake_decrypt(value: str) -> str:
    return value.removeprefix("dpapi:")[::-1]


class MuseAccountCredentialStoreTests(unittest.TestCase):
    def setUp(self):
        muse_account_auth.clear_recovery_failure()

    def test_password_credentials_are_stored_only_as_dpapi_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "muse_credentials.json"
            with (
                patch.object(muse_account_auth, "MUSE_ACCOUNT_CREDENTIALS_PATH", path),
                patch.object(muse_account_auth, "encrypt_secret", side_effect=_fake_encrypt),
                patch.object(muse_account_auth, "decrypt_secret", side_effect=_fake_decrypt),
            ):
                status = muse_account_auth.save_credentials(
                    "account-1",
                    {
                        "email": "muse@example.com",
                        "method": "password",
                        "password": "secret-password",
                        "two_factor_secret": "TOTP-SECRET",
                    },
                )
                raw_text = path.read_text(encoding="utf-8")
                document = json.loads(raw_text)
                loaded = muse_account_auth.load_credentials("account-1")

            self.assertTrue(status["auto_login_configured"])
            self.assertEqual(status["auto_login_method"], "password")
            self.assertNotIn("secret-password", raw_text)
            self.assertNotIn("TOTP-SECRET", raw_text)
            self.assertTrue(document["account-1"]["password_encrypted"].startswith("dpapi:"))
            self.assertTrue(document["account-1"]["two_factor_secret_encrypted"].startswith("dpapi:"))
            self.assertNotIn("password", document["account-1"])
            self.assertNotIn("two_factor_secret", document["account-1"])
            self.assertNotIn("mailbox_url", document["account-1"])
            self.assertEqual(loaded["password"], "secret-password")
            self.assertEqual(loaded["two_factor_secret"], "TOTP-SECRET")

    def test_mailbox_credentials_round_trip_and_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "muse_credentials.json"
            with (
                patch.object(muse_account_auth, "MUSE_ACCOUNT_CREDENTIALS_PATH", path),
                patch.object(muse_account_auth, "encrypt_secret", side_effect=_fake_encrypt),
                patch.object(muse_account_auth, "decrypt_secret", side_effect=_fake_decrypt),
            ):
                muse_account_auth.save_credentials(
                    "account-2",
                    {
                        "email": "mailbox@example.com",
                        "method": "mailbox",
                        "mailbox_url": "https://mail.example/inbox/secret",
                    },
                )
                raw_text = path.read_text(encoding="utf-8")
                loaded = muse_account_auth.load_credentials("account-2")
                removed = muse_account_auth.delete_credentials("account-2")

            self.assertNotIn("https://mail.example/inbox/secret", raw_text)
            self.assertEqual(loaded["mailbox_url"], "https://mail.example/inbox/secret")
            self.assertTrue(removed)
            self.assertFalse(path.exists())

    def test_plaintext_or_non_dpapi_secret_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "muse_credentials.json"
            path.write_text(
                json.dumps(
                    {
                        "account-1": {
                            "email": "muse@example.com",
                            "method": "password",
                            "password": "plaintext",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(muse_account_auth, "MUSE_ACCOUNT_CREDENTIALS_PATH", path):
                with self.assertRaises(SecretStorageError):
                    muse_account_auth.load_credentials("account-1")

    def test_cooldown_is_inactive_until_a_real_failure(self):
        muse_account_auth.clear_recovery_failure()
        self.assertEqual(muse_account_auth.recovery_cooldown_remaining(), 0.0)
        muse_account_auth.record_recovery_failure()
        self.assertGreater(muse_account_auth.recovery_cooldown_remaining(), 0.0)


class MuseAccountApiTests(unittest.TestCase):
    def test_validation_error_does_not_echo_secret_input(self):
        request = main.Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "PUT",
                "scheme": "http",
                "path": "/api/muse/accounts/account-1/auto-login",
                "raw_path": b"/api/muse/accounts/account-1/auto-login",
                "query_string": b"",
                "headers": [],
                "client": ("test", 123),
                "server": ("test", 80),
                "root_path": "",
            }
        )
        error = main.RequestValidationError(
            [
                {
                    "type": "literal_error",
                    "loc": ("body", "method"),
                    "msg": "invalid method",
                    "input": "SECRET-PASSWORD-TOTP",
                }
            ]
        )

        response = asyncio.run(main.handle_request_validation_error(request, error))

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("SECRET-PASSWORD-TOTP", response.body.decode("utf-8"))

    def test_account_status_uses_canonical_expiry_not_smallest_cookie_expiry(self):
        now = int(time.time())
        account = {
            "id": "account-1",
            "label": "muse@example.com",
            "enabled": True,
            "ok": True,
            "expires_at": now + 3600,
            "cookies": {"hatch_vml": "secret", "short_cookie": "secret"},
            "cookies_exp": {"hatch_vml": now + 1800, "short_cookie": now - 10},
        }
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                main.muse_settings,
                "load_muse_settings",
                return_value={"muse_studio_path": temp_dir},
            ),
            patch.object(main.muse_studio_service, "_studio_data_dir", return_value=Path(temp_dir)),
            patch.object(main.muse_account_auth, "list_statuses", return_value={}),
        ):
            (Path(temp_dir) / "accounts.json").write_text(
                json.dumps([account]), encoding="utf-8"
            )
            sanitized = main._load_sanitized_muse_accounts()[0]

        self.assertFalse(sanitized["is_expired"])
        self.assertFalse(sanitized["needs_relogin"])
        self.assertTrue(sanitized["session_verified"])
        self.assertEqual(sanitized["verification_state"], "verified")
        self.assertEqual(sanitized["expires_at"], float(now + 3600))
        self.assertNotIn("cookies", sanitized)

    def test_account_without_session_cookie_needs_relogin(self):
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                main.muse_settings,
                "load_muse_settings",
                return_value={"muse_studio_path": temp_dir},
            ),
            patch.object(main.muse_studio_service, "_studio_data_dir", return_value=Path(temp_dir)),
            patch.object(main.muse_account_auth, "list_statuses", return_value={}),
        ):
            (Path(temp_dir) / "accounts.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "account-no-cookie",
                            "label": "muse@example.com",
                            "enabled": True,
                            "ok": None,
                            "cookies": {},
                        }
                    ]
                ),
                encoding="utf-8",
            )
            sanitized = main._load_sanitized_muse_accounts()[0]

        self.assertTrue(sanitized["needs_relogin"])

    def test_partial_session_marked_ok_still_needs_relogin(self):
        now = int(time.time())
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                main.muse_settings,
                "load_muse_settings",
                return_value={"muse_studio_path": temp_dir},
            ),
            patch.object(main.muse_studio_service, "_studio_data_dir", return_value=Path(temp_dir)),
            patch.object(main.muse_account_auth, "list_statuses", return_value={}),
        ):
            (Path(temp_dir) / "accounts.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "partial-session",
                            "label": "muse@example.com",
                            "enabled": True,
                            "ok": True,
                            "cookies": {"hatch_sess": "secret"},
                            "cookies_exp": {},
                            "expires_at": now + 3600,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            sanitized = main._load_sanitized_muse_accounts()[0]

        self.assertTrue(sanitized["needs_relogin"])
        self.assertFalse(sanitized["session_verified"])
        self.assertTrue(sanitized["expires_estimated"])
        self.assertEqual(sanitized["verification_state"], "unverified")

    def test_configure_verifies_before_saving_and_returns_no_secrets(self):
        request = main.MuseAutoLoginData(
            email="muse@example.com",
            method="password",
            password="secret-password",
            two_factor_secret="TOTP-SECRET",
        )
        account = {"id": "account-1", "needs_relogin": False}
        safe_status = {
            "auto_login_configured": True,
            "auto_login_method": "password",
            "login_email": "muse@example.com",
            "totp_configured": True,
        }
        with (
            patch.object(main, "_get_sanitized_muse_account", return_value=account),
            patch.object(main, "_relogin_muse_account", return_value={"ok": True}) as relogin,
            patch.object(main.muse_account_auth, "save_credentials", return_value=safe_status) as save,
        ):
            response = main.configure_muse_account_auto_login("account-1", request)

        relogin.assert_called_once()
        save.assert_called_once()
        body = response.body.decode("utf-8")
        self.assertNotIn("secret-password", body)
        self.assertNotIn("TOTP-SECRET", body)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_configure_failure_never_persists_credentials(self):
        request = main.MuseAutoLoginData(
            email="muse@example.com",
            method="password",
            password="secret-password",
        )
        with (
            patch.object(main, "_get_sanitized_muse_account", return_value={"id": "account-1"}),
            patch.object(
                main,
                "_relogin_muse_account",
                side_effect=muse_provider.MuseAuthenticationError("invalid"),
            ),
            patch.object(main.muse_account_auth, "save_credentials") as save,
        ):
            with self.assertRaises(main.HTTPException) as raised:
                main.configure_muse_account_auto_login("account-1", request)

        save.assert_not_called()
        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.headers["Cache-Control"], "no-store")

    def test_invalid_account_id_is_rejected_before_service_start(self):
        request = main.MuseAutoLoginData(
            email="muse@example.com",
            method="password",
            password="secret-password",
        )
        with (
            patch.object(main, "_get_sanitized_muse_account") as get_account,
            patch.object(main.muse_studio_service, "acquire") as acquire,
        ):
            with self.assertRaises(main.HTTPException) as raised:
                main.configure_muse_account_auto_login("../invalid", request)

        self.assertEqual(raised.exception.status_code, 400)
        get_account.assert_not_called()
        acquire.assert_not_called()

    def test_old_muse_capability_fails_clearly_without_calling_relogin(self):
        with (
            patch.object(main.muse_account_auth, "acquire_login_slot", return_value=True),
            patch.object(main.muse_account_auth, "release_login_slot"),
            patch.object(main.muse_studio_service, "acquire", return_value={"account_relogin": 1}),
            patch.object(main.muse_studio_service, "release"),
            patch.object(main.muse_provider, "relogin_account") as relogin,
        ):
            with self.assertRaises(main.muse_studio_service.MuseStudioConfigurationError) as raised:
                main._relogin_muse_account(
                    "account-1",
                    {"email": "muse@example.com", "method": "password", "password": "secret"},
                    reject_claimable_jobs=False,
                )

        self.assertIn("account_relogin", str(raised.exception))
        relogin.assert_not_called()


class MuseAutoLoginRecoveryTests(unittest.TestCase):
    def setUp(self):
        muse_account_auth.clear_recovery_failure()

    @staticmethod
    def _candidate(account_id: str) -> dict:
        return {
            "id": account_id,
            "enabled": True,
            "needs_relogin": True,
            "auto_login_configured": True,
            "session_ok": False,
            "expires_at": 1,
        }

    def test_cycle_tries_each_candidate_once_and_stops_at_first_success(self):
        accounts = [self._candidate("account-1"), self._candidate("account-2"), self._candidate("account-3")]
        credentials = {"email": "muse@example.com", "method": "password", "password": "secret"}
        with (
            patch.object(main.muse_account_auth, "recovery_cooldown_remaining", return_value=0),
            patch.object(main, "_load_sanitized_muse_accounts", return_value=accounts),
            patch.object(main.muse_account_auth, "load_credentials", return_value=credentials),
            patch.object(
                main,
                "_relogin_muse_account",
                side_effect=[muse_provider.MuseAuthenticationError("invalid"), {"ok": True}],
            ) as relogin,
            patch.object(main.muse_account_auth, "clear_recovery_failure") as clear,
            patch.object(main.muse_account_auth, "record_recovery_failure") as record,
        ):
            result = main._run_muse_auto_login_cycle()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["account_id"], "account-2")
        self.assertEqual(result["attempted"], 2)
        self.assertEqual(
            [call.args[0] for call in relogin.call_args_list],
            ["account-1", "account-2"],
        )
        clear.assert_called_once()
        record.assert_not_called()

    def test_failed_cycle_sets_global_cooldown_but_missing_credentials_does_not(self):
        credentials = {"email": "muse@example.com", "method": "password", "password": "secret"}
        with (
            patch.object(main.muse_account_auth, "recovery_cooldown_remaining", return_value=0),
            patch.object(main, "_load_sanitized_muse_accounts", return_value=[self._candidate("account-1")]),
            patch.object(main.muse_account_auth, "load_credentials", return_value=credentials),
            patch.object(
                main,
                "_relogin_muse_account",
                side_effect=muse_provider.MuseAuthenticationError("invalid"),
            ),
            patch.object(main.muse_account_auth, "record_recovery_failure") as record,
        ):
            result = main._run_muse_auto_login_cycle()
        self.assertEqual(result["status"], "failed")
        record.assert_called_once()

        with (
            patch.object(main.muse_account_auth, "recovery_cooldown_remaining", return_value=0),
            patch.object(main, "_load_sanitized_muse_accounts", return_value=[]),
            patch.object(main, "_relogin_muse_account") as relogin,
            patch.object(main.muse_account_auth, "record_recovery_failure") as record,
        ):
            result = main._run_muse_auto_login_cycle()
        self.assertEqual(result["status"], "missing_credentials")
        relogin.assert_not_called()
        record.assert_not_called()

    def test_auth_recovery_requeues_once_and_preserves_checkpoint(self):
        job = {
            "id": "muse-auth-job",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "result": {
                "recovery_epoch": 0,
                "muse_checkpoint": {"scene": 4, "artifact_id": 88},
            },
        }
        with (
            patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
            patch.object(main.db, "get_system_job", return_value=job),
            patch.object(main, "_run_muse_auto_login_cycle", return_value={"status": "success", "attempted": 1, "account_id": "account-1"}) as cycle,
            patch.object(main.db, "update_system_job") as update_job,
            patch.object(main.db, "update_video_production_state"),
        ):
            main._handle_production_job_error(
                job, muse_provider.MuseAuthenticationError("expired")
            )

        cycle.assert_called_once()
        queued = next(call for call in update_job.call_args_list if call.kwargs.get("status") == "queued")
        result = queued.kwargs["result_json"]
        self.assertEqual(result["muse_checkpoint"], {"scene": 4, "artifact_id": 88})
        self.assertEqual(result["recovery_epoch"], 0)
        self.assertEqual(result["muse_auto_login"]["attempt"], 1)
        self.assertEqual(result["automatic_muse_login"], "succeeded")

    def test_second_auth_error_in_same_epoch_pauses_without_another_cycle(self):
        job = {
            "id": "muse-loop-guard",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "result": {
                "recovery_epoch": 0,
                "muse_checkpoint": {"scene": 4},
                "muse_auto_login": {"recovery_epoch": 0, "attempt": 1, "state": "success"},
            },
        }
        with (
            patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
            patch.object(main.db, "get_system_job", return_value=job),
            patch.object(main, "_run_muse_auto_login_cycle") as cycle,
            patch.object(main.db, "update_system_job") as update_job,
            patch.object(main.db, "update_video_production_state"),
        ):
            main._handle_production_job_error(
                job, muse_provider.MuseAuthenticationError("expired again")
            )

        cycle.assert_not_called()
        paused = next(call for call in update_job.call_args_list if call.kwargs.get("status") == "paused")
        result = paused.kwargs["result_json"]
        self.assertEqual(result["muse_checkpoint"], {"scene": 4})
        self.assertEqual(result["automatic_muse_login"], "loop_guard")
        self.assertEqual(result["attention_required"], "muse_login_required")


if __name__ == "__main__":
    unittest.main()
