#!/usr/bin/env python3
"""JSON login bridge. Sensitive input may be sent on stdin instead of argv."""
import contextlib
import json
import sys

from hotstar_auth import AuthError, begin_login, finish_login, token_status, tok_remaining


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    command = args.pop(0) if args else "status"
    try:
        data = {}
        if not args and command != "status" and not sys.stdin.isatty():
            try:
                data = json.load(sys.stdin)
            except (ValueError, TypeError):
                raise AuthError("Expected a JSON object on stdin.", "invalid_input") from None
            if not isinstance(data, dict):
                raise AuthError("Expected a JSON object on stdin.", "invalid_input")
        with contextlib.redirect_stdout(sys.stderr):
            if command == "status":
                result = token_status()
            elif command == "send-otp":
                begin_login(args[0] if args else data.get("phone"))
                result = {"success": True, "status": "otp_sent"}
            elif command == "verify-otp":
                phone = args[0] if args else data.get("phone")
                otp = args[1] if len(args) > 1 else data.get("otp")
                token = finish_login(phone, otp)
                result = {"success": True, "remaining": tok_remaining(token), "status": "logged_in"}
            else:
                raise AuthError("Usage: auth_cli.py <status|send-otp|verify-otp> [phone] [otp]", "invalid_command")
        print(json.dumps(result))
        return 0
    except AuthError as exc:
        print(json.dumps({"error": str(exc), "code": exc.code}))
        return 1
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"error": "Could not read or save the login session. Request another OTP.", "code": "session_error"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
