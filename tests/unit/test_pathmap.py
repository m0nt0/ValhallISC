import pytest
from hypothesis import given
from hypothesis import strategies as st

from irisfs.vfs.pathmap import doc_to_path, is_xml_name, path_to_doc

EXAMPLES = [
    ("Demo.Sub.Thing.cls", ("Demo", "Sub", "Thing.cls.xml")),
    ("Demo.Person.cls", ("Demo", "Person.cls.xml")),
    ("DEMORTN.mac", ("DEMORTN.mac.xml",)),
    ("Demo.Rtn2.mac", ("Demo", "Rtn2.mac.xml")),
    ("DemoInc.inc", ("DemoInc.inc.xml",)),
    ("DemoTable.LUT", ("DemoTable.LUT.xml",)),
    ("%Library.String.cls", ("%Library", "String.cls.xml")),
    ("Ens-Analytics-X.dashboard.DFI", ("Ens-Analytics-X", "dashboard.DFI.xml")),
]


@pytest.mark.parametrize(("name", "parts"), EXAMPLES)
def test_examples_both_ways(name: str, parts: tuple[str, ...]) -> None:
    assert doc_to_path(name) == parts
    assert path_to_doc(parts) == name


@pytest.mark.parametrize(
    "name", ["/csp/user/menu.csp", "noext", ".cls", "A..B.cls", "A.B.", "A/B.cls", "A.B.c-s"]
)
def test_unmappable_names(name: str) -> None:
    assert doc_to_path(name) is None


@pytest.mark.parametrize(
    "parts",
    [
        (),
        ("Demo",),
        ("Demo", "Person.cls"),  # no .xml suffix
        ("Demo", "Person.xml"),  # no item extension
        ("Demo.Sub", "Thing.cls.xml"),  # dotted directory is not canonical
        ("Demo", "Sub.Thing.cls.xml"),  # dotted stem is not canonical
        ("", "Person.cls.xml"),
    ],
)
def test_non_canonical_paths_rejected(parts: tuple[str, ...]) -> None:
    assert path_to_doc(parts) is None


segment = st.text(
    alphabet=st.characters(blacklist_characters="./\\", blacklist_categories=("Cc", "Cs")), min_size=1
)
ext = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=5
)


@given(st.lists(segment, min_size=1, max_size=5), ext)
def test_bijection_property(segments: list[str], extension: str) -> None:
    name = ".".join(segments) + "." + extension
    parts = doc_to_path(name)
    assert parts is not None
    assert path_to_doc(parts) == name


def test_is_xml_name() -> None:
    assert is_xml_name("a.xml") and is_xml_name("A.CLS.XML")
    assert not is_xml_name(".xml") and not is_xml_name("a.txt") and not is_xml_name("xml")
