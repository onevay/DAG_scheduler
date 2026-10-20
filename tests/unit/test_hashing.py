import pytest

from expsched.contract.hashing import canonical_hash, canonical_json


def test_key_order_and_whitespace_do_not_matter():
    assert canonical_hash({"b": 1, "a": [1, 2]}) == canonical_hash({"a": [1, 2], "b": 1})


def test_canonical_form_is_compact_sorted_utf8():
    assert canonical_json({"b": "ж", "a": 1}) == '{"a":1,"b":"ж"}'


def test_known_value():
    assert canonical_hash({}) == "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"


def test_different_content_gives_different_hash():
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


@pytest.mark.parametrize("bad", [float("nan"), object()])
def test_unserializable_values_fail_loudly(bad):
    with pytest.raises((ValueError, TypeError)):
        canonical_hash({"x": bad})
