import unittest

from hotstar_proto import ProtoDecodeError, decode_message, extract_texts


def varint(value):
    result = bytearray()
    while value > 0x7f:
        result.append((value & 0x7f) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def blob(field, value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return varint(field << 3 | 2) + varint(len(value)) + value


class ProtoTests(unittest.TestCase):
    def test_decodes_supported_wire_types_without_guessing_semantics(self):
        payload = b"\x08" + varint(300) + b"\x11" + (123456789).to_bytes(8, "little")
        payload += blob(3, b"\x00\xff") + b"\x25" + (42).to_bytes(4, "little")
        self.assertEqual(decode_message(payload), [
            {"field": 1, "wire": 0, "value": 300},
            {"field": 2, "wire": 1, "value": 123456789},
            {"field": 3, "wire": 2, "value": b"\x00\xff"},
            {"field": 4, "wire": 5, "value": 42},
        ])

    def test_extracts_text_from_nested_synthetic_response_envelopes(self):
        payload = blob(1, blob(2, blob(3, "Request accepted")) + blob(4, "status"))
        self.assertEqual(extract_texts(payload), ["Request accepted", "status"])
        error = blob(2, blob(1, "INVALID_REQUEST") + blob(2, "Please try again later"))
        self.assertEqual(extract_texts(error), ["INVALID_REQUEST", "Please try again later"])

    def test_utf8_and_plain_strings_are_not_guessed_as_nested_messages(self):
        # Five ASCII bytes can also form a syntactically valid fixed32 field.
        self.assertEqual(extract_texts(blob(1, "Error") + blob(2, "अनुरोध विफल") + blob(3, "échec")),
                         ["Error", "अनुरोध विफल", "échec"])

    def test_opaque_binary_empty_and_numeric_fields_do_not_become_text(self):
        payload = blob(1, b"\x00\xff") + blob(2, b"") + b"\x18\x01"
        self.assertEqual(extract_texts(payload), [])
        self.assertEqual(decode_message(b""), [])

    def test_truncated_and_invalid_wire_fields_raise(self):
        for payload in (b"\x08\x80", b"\x12\x08short", b"\x09abc", b"\x15abc",
                        b"\x00", b"\x0b", b"\x0c", b"\x0f", b"\x08" + b"\xff" * 10,
                        varint(0x20000000 << 3) + b"\x01"):
            with self.subTest(payload=payload), self.assertRaises(ProtoDecodeError):
                decode_message(payload)
        with self.assertRaises(ProtoDecodeError):
            extract_texts(b"\x12\x08short")

    def test_maximum_unsigned_varint_is_valid(self):
        self.assertEqual(decode_message(b"\x08" + varint(2**64 - 1))[0]["value"], 2**64 - 1)

    def test_size_field_text_and_nesting_limits_are_enforced(self):
        with self.assertRaises(ProtoDecodeError):
            decode_message(blob(1, "hello"), max_bytes=3)
        with self.assertRaises(ProtoDecodeError):
            decode_message(b"\x08\x01\x08\x02", max_fields=1)
        with self.assertRaises(ProtoDecodeError):
            extract_texts(blob(1, "too long"), max_text_length=3)
        with self.assertRaises(ProtoDecodeError):
            extract_texts(blob(1, "first") + blob(2, "second"), max_texts=1)
        nested = blob(1, blob(1, blob(1, "message")))
        with self.assertRaises(ProtoDecodeError):
            extract_texts(nested, max_depth=1)
        with self.assertRaises(ProtoDecodeError):
            extract_texts(nested, max_fields=2)
        with self.assertRaises(ProtoDecodeError):
            extract_texts(blob(1, b"\x08\x01" * 3), max_fields=2)
        self.assertEqual(extract_texts(nested, max_depth=2), ["message"])

    def test_input_type_and_negative_traversal_limits_are_rejected(self):
        with self.assertRaises(TypeError):
            decode_message("not bytes")
        with self.assertRaises(ValueError):
            extract_texts(b"", max_depth=-1)
        self.assertEqual(decode_message(memoryview(b"\x08\x01"))[0]["value"], 1)


if __name__ == "__main__":
    unittest.main()
