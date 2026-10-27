#!/usr/bin/env python3
"""Interactive login using the same session as node index.js and the relay."""
from getpass import getpass

from hotstar_auth import AuthError, begin_login, finish_login, normalize_phone, tok_remaining


def main():
    print("Hotstar login")
    try:
        phone = normalize_phone(input("Indian mobile number: "))
        print("Requesting OTP...")
        begin_login(phone)
        otp = getpass("OTP: ")
        token = finish_login(phone, otp)
        print(f"Logged in. {tok_remaining(token)}. Run node index.js in this folder.")
        return 0
    except AuthError as exc:
        print(str(exc))
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nLogin cancelled.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
