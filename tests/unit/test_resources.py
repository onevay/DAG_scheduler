import pytest

from expsched.domain.resources import Resources


def R(**kw: int) -> Resources:
    return Resources(kw)


def test_zero_components_are_not_stored():
    assert R(gpu=0) == Resources()
    assert hash(R(gpu=0, cpu=2)) == hash(R(cpu=2))
    assert not Resources()


def test_missing_resource_is_zero():
    assert R(gpu=2).get("ram_gb") == 0


def test_fits_is_componentwise_partial_order():
    assert R(gpu=2).fits(R(gpu=4, cpu=8))
    assert Resources().fits(Resources())
    assert not R(gpu=2).fits(R(cpu=64))  # ресурса gpu у узла нет
    a, b = R(gpu=4), R(cpu=4)
    assert not a.fits(b) and not b.fits(a)  # порядок частичный


def test_add_and_sub_roundtrip():
    a, b = R(gpu=4, cpu=2), R(gpu=1, ram_gb=8)
    assert (a + b) - b == a
    assert (a + b).get("ram_gb") == 8


def test_sub_below_zero_raises():
    with pytest.raises(ValueError, match="negative"):
        R(gpu=1) - R(gpu=2)


@pytest.mark.parametrize("bad", [-1, 1.5, True, "2"])
def test_invalid_component_is_rejected(bad):
    with pytest.raises(ValueError):
        Resources({"gpu": bad})


def test_is_immutable_and_iterates_sorted_keys():
    r = R(gpu=1, cpu=2)
    with pytest.raises(AttributeError):
        r.x = 1  # type: ignore[attr-defined]
    assert list(r) == ["cpu", "gpu"]
    assert repr(r) == "{cpu: 2, gpu: 1}"
