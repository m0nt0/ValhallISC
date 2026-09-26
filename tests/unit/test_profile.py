import pytest

from irisfs.config.profile import Profile, ProfileValidationError


def make(**kw: object) -> Profile:
    base: dict[str, object] = dict(
        name="Local", host="127.0.0.1", username="_SYSTEM", mount_point="/tmp/iris"
    )
    base.update(kw)
    return Profile(**base)  # type: ignore[arg-type]


def test_valid_profile_has_no_errors() -> None:
    assert make().validate("Darwin") == {}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("name", "x" * 65),
        ("host", ""),
        ("host", "http://server"),
        ("host", "server/path"),
        ("host", "has space"),
        ("port", 0),
        ("port", 65536),
        ("username", "  "),
        ("path_prefix", "iris"),
        ("path_prefix", "/iris/"),
        ("compile_flags", "c k"),
        ("mount_point", ""),
        ("mount_point", "relative/dir"),
    ],
)
def test_invalid_fields(field: str, value: object) -> None:
    errors = make(**{field: value}).validate("Linux")
    assert field in errors


@pytest.mark.parametrize("host", ["iris.example.com", "10.0.0.5", "[::1]", "my-host_1"])
def test_valid_hosts(host: str) -> None:
    assert make(host=host).validate("Linux") == {}


@pytest.mark.parametrize("mp", ["X:", "x:\\", "C:\\Users\\me\\iris"])
def test_windows_mount_points_valid(mp: str) -> None:
    assert make(mount_point=mp).validate("Windows") == {}


@pytest.mark.parametrize("mp", ["/tmp/iris", "iris", "\\\\server\\share"])
def test_windows_mount_points_invalid(mp: str) -> None:
    assert "mount_point" in make(mount_point=mp).validate("Windows")


def test_check_raises_with_errors() -> None:
    with pytest.raises(ProfileValidationError) as exc:
        make(name="", port=0).check("Linux")
    assert set(exc.value.errors) == {"name", "port"}


def test_base_url() -> None:
    assert make().base_url == "http://127.0.0.1:52773"
    assert make(https=True, port=443, path_prefix="/iris").base_url == "https://127.0.0.1:443/iris"


def test_dict_round_trip_and_unknown_keys_ignored() -> None:
    p = make(read_only=True)
    data = p.to_dict() | {"future_field": 42}
    assert Profile.from_dict(data) == p


def test_ids_are_unique() -> None:
    assert make().id != make().id
