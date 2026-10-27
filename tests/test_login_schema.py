import unittest

from hotstar_login_schema import binary_error, find_widget_action, otp_confirmed, provider_error


def blob(field, value):
    value = value.encode() if isinstance(value, str) else value
    return bytes([field << 3 | 2, len(value)]) + value


class LoginSchemaTests(unittest.TestCase):
    def test_top_level_and_nested_error_fields_keep_code_and_message(self):
        self.assertEqual(provider_error({"error_code": "ERR_UNKNOWN", "error_message": "Something went wrong!"}),
                         "ERR_UNKNOWN: Something went wrong!")
        self.assertEqual(provider_error({"success": {"page": {"error": {"code": "RATE_LIMIT", "message": "Wait"}}}}),
                         "RATE_LIMIT: Wait")
        self.assertEqual(provider_error({"errors": [{"description": "Try later"}]}), "Try later")
        self.assertEqual(provider_error({"page": [{"error_message": "Wrong code"}]}), "Wrong code")
        self.assertEqual(provider_error({"success": False, "message": "Invalid request"}), "Invalid request")

    def test_non_errors_and_arbitrary_messages_are_not_failures(self):
        for data in ({"success": {}}, {"error_code": 0, "error_message": ""}, {"error": None},
                     {"error": "none"}, {"message": "Welcome"}, {"error": {"code": "SUCCESS"}}, None):
            with self.subTest(data=data):
                self.assertIsNone(provider_error(data))

    def test_binary_error_reads_specific_error_envelope(self):
        payload = blob(2, blob(1, "ERR_UNKNOWN") + blob(2, "Something went wrong!"))
        self.assertEqual(binary_error(payload), "ERR_UNKNOWN: Something went wrong!")
        self.assertIsNone(binary_error(blob(1, blob(1, "ERR_UNKNOWN") + blob(2, "not an error envelope"))))
        self.assertIsNone(binary_error(b"\x12\xff"))
        self.assertIsNone(binary_error(b"\x12\x00"))
        self.assertIsNone(binary_error(b"\x08\x01"))

    def test_verify_widget_types_and_template_names_confirm_request(self):
        for data in (
            {"template": "VerifyOtpWidget"},
            {"@type": "type.googleapis.com/feature.login.VerifyOtpWidget"},
            {"template": {"name": "VerifyOtpWidget"}},
            {"success": {"page": {"widgets": [{"template": {"name": "feature.login.VerifyOtpWidget"}}]}}},
            {"status": "OTP_SENT"}, {"status": "otp_sent"}, {"result": {"otp_sent": True}},
        ):
            with self.subTest(data=data):
                self.assertTrue(otp_confirmed(data))

    def test_http_success_empty_and_phone_or_captcha_widgets_do_not_confirm(self):
        for data in ({}, {"success": {}}, {"status": "SUCCESS"}, {"otp_sent": "true"},
                     {"template": "LoginWithPhoneWidget"}, {"template": "CaptchaWidget"},
                     {"note": "VerifyOtpWidget"}, {"@type": "NotVerifyOtpWidget"},
                     {"status": "OTP_SENT", "error": {"message": "Rejected"}}, None):
            with self.subTest(data=data):
                self.assertFalse(otp_confirmed(data))

    def test_widget_action_uses_url_query_and_exact_action(self):
        send = "/bff/v2/pages/1/spaces/1/widgets/8?action=sendOtp&pageRef=myspace"
        verify = "/bff/v2/pages/1/spaces/1/widgets/9?x=1&action=verifyOtp"
        data = {"success": {"items": [{"fetch_widget": {"url": send}}, {"more": {"fetch_widget": {"url": verify}}}]}}
        self.assertEqual(find_widget_action(data, "sendOtp"), send)
        self.assertEqual(find_widget_action(data, "verifyOtp"), verify)
        self.assertIsNone(find_widget_action(data, "resendOtp"))

    def test_widget_actions_reject_external_ambiguous_and_malformed_urls(self):
        for url in ("https://example.test/x?action=sendOtp", "//example.test/x?action=sendOtp",
                    "/x?action=sendOtpAgain", "/x?action=sendOtp&action=verifyOtp",
                    "/x#action=sendOtp", "/x?action=sendOtp\n", "/\\example.test/x?action=sendOtp"):
            with self.subTest(url=url):
                self.assertIsNone(find_widget_action({"fetch_widget": {"url": url}}, "sendOtp"))

    def test_container_walk_is_bounded_for_cycles(self):
        data = {"items": []}
        data["items"].append(data)
        self.assertIsNone(provider_error(data))
        self.assertFalse(otp_confirmed(data))
        self.assertIsNone(find_widget_action(data, "sendOtp"))
        cyclic_error = []
        cyclic_error.append(cyclic_error)
        self.assertIsNone(provider_error({"error": cyclic_error}))


if __name__ == "__main__":
    unittest.main()
