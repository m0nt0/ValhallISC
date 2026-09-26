import pytest

from irisfs.vfs.xmlexport import InvalidExport, validate

HEAD = b'<?xml version="1.0" encoding="UTF-8"?>\n'


def test_manifest_lists_items() -> None:
    data = HEAD + (
        b'<Export generator="IRIS" version="26">'
        b'<Class name="A.B"/><Routine name="R1" type="MAC"/><Routine name="I1" type="INC"/>'
        b'<Document name="T.LUT"/><Project name="p"/></Export>'
    )
    assert validate(data).items == ("A.B.cls", "R1.mac", "I1.inc", "T.LUT")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "empty"),
        (b"   \n", "empty"),
        (b"<Export><Class name='x'>", "well-formed"),
        (b"hello", "well-formed"),
        (b"<foo/>", "root element is <foo>"),
        (b'<Export generator="IRIS"/>', "no classes"),
        (b'<Export generator="IRIS"><Global name="^X"/></Export>', "unsupported elements: Global"),
    ],
)
def test_invalid(data: bytes, message: str) -> None:
    with pytest.raises(InvalidExport, match=message):
        validate(data)


def test_xxe_refused() -> None:
    data = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
        b'<Export generator="IRIS"><Class name="A">&e;</Class></Export>'
    )
    with pytest.raises(InvalidExport, match="unsafe"):
        validate(data)


def test_billion_laughs_refused() -> None:
    data = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
        b'<Export generator="IRIS"><Class name="A">&lol2;</Class></Export>'
    )
    with pytest.raises(InvalidExport, match="unsafe"):
        validate(data)
