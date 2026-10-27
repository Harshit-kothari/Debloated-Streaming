"""Small, bounded protobuf wire decoder for provider response diagnostics.

No schema or success/error meaning is inferred. Decoded bytes and text can contain
credentials; callers must sanitize them before displaying or saving diagnostics.
"""


class ProtoDecodeError(ValueError):
    """The payload is malformed, unsupported, or exceeds a decoding limit."""


class _LimitError(ProtoDecodeError):
    """A limit violation must not be mistaken for an opaque binary leaf."""


def _data_bytes(data, max_bytes):
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("Protobuf input must be bytes-like.")
    if max_bytes < 0 or len(data) > max_bytes:
        raise _LimitError("Protobuf response exceeds the byte limit.")
    return bytes(data)


def _varint(data, offset):
    value = 0
    for index in range(10):
        if offset >= len(data):
            raise ProtoDecodeError("Truncated protobuf varint.")
        byte = data[offset]
        offset += 1
        if index == 9 and byte > 1:
            raise ProtoDecodeError("Protobuf varint exceeds 64 bits.")
        value |= (byte & 0x7f) << (index * 7)
        if not byte & 0x80:
            return value, offset
    raise ProtoDecodeError("Protobuf varint exceeds 64 bits.")


def decode_message(data, *, max_bytes=1048576, max_fields=2048):
    """Return wire fields as {field, wire, value}; length-delimited values stay bytes.

    Supported wire types are varint (0), fixed64 (1), length-delimited (2), and
    fixed32 (5). Group fields (3/4) are deliberately rejected.
    """
    data = _data_bytes(data, max_bytes)
    if max_fields < 0:
        raise ValueError("max_fields must be nonnegative.")
    fields = []
    offset = 0
    while offset < len(data):
        if len(fields) >= max_fields:
            raise _LimitError("Protobuf response exceeds the field limit.")
        key, offset = _varint(data, offset)
        number, wire = key >> 3, key & 7
        if not 0 < number <= 0x1fffffff:
            raise ProtoDecodeError("Invalid protobuf field number.")
        if wire == 0:
            value, offset = _varint(data, offset)
        elif wire in (1, 5):
            width = 8 if wire == 1 else 4
            if offset + width > len(data):
                raise ProtoDecodeError("Truncated protobuf fixed-width field.")
            value = int.from_bytes(data[offset:offset + width], "little")
            offset += width
        elif wire == 2:
            length, offset = _varint(data, offset)
            if length > len(data) - offset:
                raise ProtoDecodeError("Truncated protobuf length-delimited field.")
            value = data[offset:offset + length]
            offset += length
        else:
            raise ProtoDecodeError("Unsupported protobuf wire type.")
        fields.append({"field": number, "wire": wire, "value": value})
    return fields


def _readable_text(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text and all(character.isprintable() or character in "\r\n\t" for character in text):
        return text
    return None


def extract_texts(data, *, max_depth=8, max_fields=2048, max_texts=128,
                  max_text_length=4096, max_bytes=1048576):
    """Extract readable UTF-8 strings from nested length-delimited fields.

    Invalid top-level messages raise ProtoDecodeError. Non-text binary fields that
    are not nested messages are ignored. Limits apply across the whole traversal;
    exceeding a limit raises instead of returning an incomplete diagnostic.
    """
    data = _data_bytes(data, max_bytes)
    if min(max_depth, max_fields, max_texts, max_text_length) < 0:
        raise ValueError("Protobuf traversal limits must be nonnegative.")
    texts = []
    remaining = max_fields

    def visit(fields, depth):
        nonlocal remaining
        remaining -= len(fields)
        if remaining < 0:
            raise _LimitError("Protobuf response exceeds the field limit.")
        for field in fields:
            if field["wire"] != 2 or not field["value"]:
                continue
            value = field["value"]
            text = _readable_text(value)
            if text is not None:
                if len(text) > max_text_length:
                    raise _LimitError("Protobuf text exceeds the length limit.")
                if len(texts) >= max_texts:
                    raise _LimitError("Protobuf response exceeds the text count limit.")
                texts.append(text)
                continue
            try:
                nested = decode_message(value, max_bytes=max_bytes, max_fields=max_fields)
            except _LimitError:
                raise
            except ProtoDecodeError:
                continue
            if depth >= max_depth:
                raise _LimitError("Protobuf response exceeds the nesting limit.")
            visit(nested, depth + 1)

    visit(decode_message(data, max_bytes=max_bytes, max_fields=max_fields), 0)
    return texts
