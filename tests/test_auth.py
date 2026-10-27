import base64
import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

import auth_cli
import hotstar_auth as auth


def jwt(**claims):
    payload = {"exp": int(time.time()) + 3600, **claims}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode().rstrip("=")
    return "eyJhbGciOiJIUzI1NiJ9." + encoded + ".test_signature"


def response(data=None, token=None, status=200, ps=None):
    item = Mock(status_code=status)
    item.headers = requests.structures.CaseInsensitiveDict()
    if token:
        item.headers["x-hs-updatedusertoken"] = token
    if ps:
        item.headers["x-hs-setproxystate"] = ps
    item.content = b"protobuf" if data is None else json.dumps(data).encode()
    if data is None:
        item.json.side_effect = ValueError("not JSON")
    else:
        item.json.return_value = data
    return item


def login_page(send_url="/v2/pages/1/spaces/1/widgets/8?action=sendOtp&page_enum=login"):
    return {"success": {"page": {"widget_wrapper": {"template": "LoginWithPhoneWidget", "widget": {
        "data": {"send_otp_button": {"actions": {"on_click": [{"fetch_widget": {"url": send_url}}]}}}}}}}}


def otp_page(verify_url="/v2/pages/1/spaces/1/widgets/9?action=verifyOtp&page_enum=login"):
    return {"success": {"widget_wrapper": {"template": "VerifyOtpWidget", "widget": {
        "data": {"verify_button": {"actions": {"on_click": [{"fetch_widget": {"url": verify_url}}]}}}}}}}


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.directory.cleanup)
        self.session_path = Path(self.directory.name) / "session.json"
        self.token_path = Path(self.directory.name) / "token.json"
        self.guest = jwt(sub=json.dumps({"deviceId": "test-device"}), userType="guest")
        self.rotated_guest = jwt(sub=json.dumps({"deviceId": "test-device"}), userType="guest", revision=2)
        self.account = jwt(sub=json.dumps({"deviceId": "test-device", "userId": "test-account"}))
        self.headers = SimpleNamespace(
            _dl_web_hdrs=lambda **kwargs: {"kind": "web", "ps": kwargs.get("ps"), "token": kwargs.get("token")},
            _dl_android_hdrs=lambda **kwargs: {"kind": "android", "token": kwargs.get("token")})
        self.import_patch = patch("hotstar_auth.import_module", return_value=self.headers)
        self.import_patch.start()
        self.addCleanup(self.import_patch.stop)
        self.login_page_patch = patch("hotstar_auth.requests.Session.get", return_value=response(login_page()))
        self.login_page_patch.start()
        self.addCleanup(self.login_page_patch.stop)

    def test_jwt_urlsafe_payload(self):
        token = jwt(note="\uffff\uffff\uffff", userId="account")
        self.assertIn("_", token.split(".")[1])
        self.assertGreater(auth.jwt_exp(token), time.time())
        self.assertTrue(auth.authenticated_token(token))

    def test_guest_not_account(self):
        self.assertFalse(auth.authenticated_token(self.guest))
        self.assertFalse(auth.authenticated_token(self.account, self.account))
        self.assertTrue(auth.authenticated_token(self.account, self.guest))

    def test_hotstar_identity_schema_requires_verified_login(self):
        account = jwt(sub=json.dumps({"hId": "test-account", "pId": "test-profile", "isPhoneVerified": True}))
        guest = jwt(sub=json.dumps({"hId": "test-guest", "pId": "test-guest-profile", "isPhoneVerified": False}))
        self.assertTrue(auth.authenticated_token(account))
        self.assertFalse(auth.authenticated_token(guest))
        self.assertFalse(auth.authenticated_token(jwt(sub=json.dumps({"hId": "test-guest", "isPhoneVerified": True, "type": "guest"}))))

    def test_failed_otp_persists_rotated_state_for_retry(self):
        self.session_path.write_text(json.dumps({"phone": "9876543210", "guest": self.guest,
            "ps": "initial", "method": "web", "device_id": "test-device", "created_at": time.time()}))
        first, second = requests.Session(), requests.Session()
        first.cookies.set("retry_cookie", "test-value", domain="www.hotstar.com", path="/")
        first.post = Mock(return_value=response({"error": {"message": "Wrong OTP"}}, self.rotated_guest, status=401, ps="rotated"))
        second.post = Mock(return_value=response({}, self.account))
        with patch("hotstar_auth.requests.Session", side_effect=[first, second]):
            with self.assertRaises(auth.AuthError):
                auth.finish_login("9876543210", "111111", self.session_path, self.token_path)
            state = json.loads(self.session_path.read_text())
            self.assertEqual(state["guest"], self.rotated_guest)
            self.assertEqual(state["ps"], "rotated")
            self.assertFalse(self.token_path.exists())
            self.assertEqual(auth.finish_login("9876543210", "123456", self.session_path, self.token_path), self.account)
        self.assertEqual(second.post.call_args.kwargs["headers"]["token"], self.rotated_guest)
        self.assertEqual(second.cookies.get("retry_cookie"), "test-value")

    def test_persisted_session_rotations_cookies_device_and_method(self):
        first, second = requests.Session(), requests.Session()
        first.cookies.set("login_cookie", "test-value", domain="www.hotstar.com", path="/")
        first.post = Mock(side_effect=[response({}, self.guest, ps="first"),
                                       response(otp_page(), self.rotated_guest, ps="second")])
        second.post = Mock(return_value=response({"success": {}}, self.account))
        with patch("hotstar_auth.requests.Session", side_effect=[first, second]):
            state = auth.begin_login("+91 98765 43210", self.session_path)
            self.assertEqual(state["method"], "web")
            self.assertEqual(state["guest"], self.rotated_guest)
            token = auth.finish_login("9876543210", "123456", self.session_path, self.token_path)
        self.assertEqual(token, self.account)
        self.assertEqual(auth.load_token(self.token_path), self.account)
        self.assertFalse(self.session_path.exists())
        self.assertEqual(second.cookies.get("login_cookie"), "test-value")
        first_headers = first.post.call_args_list[0].kwargs["headers"]
        verify_headers = second.post.call_args.kwargs["headers"]
        self.assertEqual(first_headers["x-hs-device-id"], verify_headers["x-hs-device-id"])
        self.assertEqual(verify_headers["token"], self.rotated_guest)
        self.assertEqual(verify_headers["ps"], "second")

    def test_android_fallback_stays_android_for_send_and_verify(self):
        session = requests.Session()
        session.post = Mock(side_effect=[response({}, status=403), response(None, self.guest),
                                         response(otp_page()), response(None, self.account)])
        client = auth.LoginClient(session=session)
        _, _, method = client.guest()
        self.assertEqual(method, "android")
        self.assertEqual(client.send("9876543210"), (True, "android"))
        self.assertEqual(client.verify("9876543210", "123456"), self.account)
        self.assertTrue(all("apix.hotstar.com" in call.args[0] for call in session.post.call_args_list[1:]))

    def test_web_start_without_token_tries_android(self):
        session = requests.Session()
        session.post = Mock(side_effect=[response({"success": {}}), response(None, self.guest)])
        client = auth.LoginClient(session=session)
        self.assertEqual(client.guest()[2], "android")

    def test_android_html_success_is_rejected(self):
        session = requests.Session()
        html = response(None)
        html.content = b"<!doctype html><title>Verify browser</title>"
        session.post = Mock(return_value=html)
        client = auth.LoginClient({"guest": self.guest, "method": "android"}, session)
        with self.assertRaises(auth.AuthError) as raised:
            client.send("9876543210")
        self.assertEqual(raised.exception.code, "browser_verification")

    def test_html_success_is_not_otp_sent(self):
        session = requests.Session()
        session.post = Mock(return_value=response(None))
        client = auth.LoginClient({"guest": self.guest, "method": "web"}, session)
        with self.assertRaises(auth.AuthError) as raised:
            client.send("9876543210")
        self.assertEqual(raised.exception.code, "browser_verification")

    def test_rotated_guest_does_not_complete_verification(self):
        session = requests.Session()
        session.post = Mock(return_value=response({}, self.rotated_guest))
        client = auth.LoginClient({"guest": self.guest, "method": "web"}, session)
        with self.assertRaises(auth.AuthError) as raised:
            client.verify("9876543210", "123456")
        self.assertEqual(raised.exception.code, "verification_failed")

    def test_http_failure_cannot_supply_success_token(self):
        session = requests.Session()
        session.post = Mock(return_value=response({"error": {"message": "Wrong OTP"}}, self.account, status=401))
        client = auth.LoginClient({"guest": self.guest, "method": "web"}, session)
        with self.assertRaisesRegex(auth.AuthError, "Wrong OTP"):
            client.verify("9876543210", "123456")

    def test_provider_errors_do_not_print_jwt(self):
        problem = auth._provider_error({"error": {"message": "Rejected " + self.guest}})
        self.assertNotIn(self.guest, problem)
        self.assertIn("[redacted]", problem)

    def test_stale_or_broken_session_never_reaches_provider(self):
        for value in ("not JSON", json.dumps({"created_at": time.time() - 601, "phone": "9876543210"})):
            with self.subTest(value=value):
                self.session_path.write_text(value)
                with patch("hotstar_auth.LoginClient") as client:
                    with self.assertRaises(auth.AuthError) as raised:
                        auth.finish_login("9876543210", "123456", self.session_path, self.token_path)
                    client.assert_not_called()
                self.assertEqual(raised.exception.code, "session_expired")

    def test_input_validation(self):
        self.assertEqual(auth.normalize_phone("+91 98765 43210"), "9876543210")
        for value in ("123", "1234567890", "9e12345678", None):
            with self.subTest(value=value), self.assertRaises(auth.AuthError):
                auth.normalize_phone(value)
        with self.assertRaises(auth.AuthError):
            auth.normalize_otp("123a56")

    def test_malformed_missing_and_guest_token_status(self):
        self.assertEqual(auth.token_status(self.token_path)["status"], "none")
        self.token_path.write_text("bad JSON")
        self.assertIsNone(auth.load_token(self.token_path))
        self.token_path.write_text(json.dumps({"user_token": self.guest}))
        self.assertNotEqual(auth.token_status(self.token_path)["status"], "valid")

    def test_http200_android_error_is_not_otp_sent(self):
        session = requests.Session()
        rejected = response(None)
        rejected.content = b'\x12\x24\x0a\x0bERR_UNKNOWN\x12\x15Something went wrong!'
        session.post = Mock(return_value=rejected)
        client = auth.LoginClient({"guest": self.guest, "method": "android"}, session)
        with self.assertRaisesRegex(auth.AuthError, "ERR_UNKNOWN"):
            client.send("9876543210")
        self.assertNotIn("created_at", client.state)
        self.assertNotIn("otp_confirmed", client.state)

    def test_unconfirmed_response_does_not_create_otp_session(self):
        for body in ({"success": {}}, login_page()):
            session = requests.Session()
            session.post = Mock(return_value=response(body))
            client = auth.LoginClient({"guest": self.guest, "method": "android"}, session)
            with self.subTest(body=body), self.assertRaises(auth.AuthError) as raised:
                client.send("9876543210")
            self.assertEqual(raised.exception.code, "otp_not_confirmed")
            self.assertNotIn("created_at", client.state)

    def test_server_actions_and_typed_json_are_used_for_send_and_verify(self):
        session = requests.Session()
        send_url = "/v2/pages/42/spaces/7/widgets/101?action=sendOtp&page_enum=login"
        verify_url = "/v2/pages/42/spaces/7/widgets/102?action=verifyOtp&page_enum=login"
        session.get = Mock(return_value=response(login_page(send_url)))
        session.post = Mock(side_effect=[response(otp_page(verify_url)), response({}, self.account)])
        client = auth.LoginClient({"guest": self.guest, "method": "android"}, session)
        self.assertEqual(client.send("9876543210"), (True, "android"))
        self.assertEqual(client.verify("9876543210", "123456"), self.account)
        send, verify = session.post.call_args_list
        self.assertEqual(send.args[0], "https://apix.hotstar.com" + send_url)
        self.assertEqual(verify.args[0], "https://apix.hotstar.com" + verify_url)
        self.assertNotIn("data", send.kwargs)
        self.assertEqual(send.kwargs["json"]["body"]["@type"], "type.googleapis.com/feature.login.InitiatePhoneLoginRequest")
        self.assertEqual(send.kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(verify.kwargs["json"]["body"]["verification_code"], "123456")

    def test_error_code_and_error_message_are_reported(self):
        session = requests.Session()
        session.post = Mock(return_value=response({"error": {"error_code": "ERR_UM_092", "error_message": "Error occurred in Widget"}}))
        client = auth.LoginClient({"guest": self.guest, "method": "android"}, session)
        with self.assertRaisesRegex(auth.AuthError, "ERR_UM_092.*Error occurred in Widget"):
            client.send("9876543210")

    def test_resend_cooldown_prevents_duplicate_provider_requests(self):
        self.session_path.write_text(json.dumps({"phone": "9876543210", "otp_confirmed": True, "created_at": time.time()}))
        with patch("hotstar_auth.LoginClient") as client:
            with self.assertRaises(auth.AuthError) as raised:
                auth.begin_login("9876543210", self.session_path)
            client.assert_not_called()
        self.assertEqual(raised.exception.code, "resend_cooldown")

    def test_restart_resumes_only_confirmed_live_otp_request(self):
        state = {"phone": "9876543210", "guest": self.guest, "device_id": "test-device",
                 "method": "web", "created_at": time.time()}
        self.session_path.write_text(json.dumps(state))
        self.assertEqual(auth.pending_login(self.session_path)["status"], "none")
        state["otp_confirmed"] = True
        self.session_path.write_text(json.dumps(state))
        self.assertEqual(auth.pending_login(self.session_path)["status"], "pending")
        state["created_at"] = time.time() - 601
        self.session_path.write_text(json.dumps(state))
        self.assertEqual(auth.pending_login(self.session_path)["status"], "none")

    def test_cli_stdin_json_and_nonzero_errors(self):
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO('{"phone":"9876543210"}')), \
                patch("auth_cli.begin_login") as begin, contextlib.redirect_stdout(output):
            result = auth_cli.main(["send-otp"])
        self.assertEqual(result, 0)
        begin.assert_called_once_with("9876543210")
        self.assertEqual(json.loads(output.getvalue())["status"], "otp_sent")
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO("bad JSON")), contextlib.redirect_stdout(output):
            result = auth_cli.main(["send-otp"])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue())["code"], "invalid_input")


if __name__ == "__main__":
    unittest.main()
