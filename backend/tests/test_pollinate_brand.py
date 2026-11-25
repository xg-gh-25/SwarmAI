"""Pollinate's brand comes from the user's own config (P7).

- brand_config: shipped neutral defaults, user's pollinate-brand.yaml deep-merged on top;
  the signature resolves link_text and QR paths; nothing configured -> nothing required;
- a broken brand file raises BrandConfigError instead of crashing with a raw error.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "s_pollinate" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import brand_config  # noqa: E402

MINE = """
brand:
  name: "Acme"
signature:
  watermark: "Made by Acme"
  link: "https://acme.example/blog/"
  qr:
    dark_on_light: "qr/acme-dark.png"
"""


@pytest.fixture
def no_brand(tmp_path, monkeypatch):
    monkeypatch.setenv("POLLINATE_BRAND_FILE", str(tmp_path / "absent.yaml"))


@pytest.fixture
def my_brand(tmp_path, monkeypatch):
    f = tmp_path / "pollinate-brand.yaml"
    f.write_text(MINE)
    monkeypatch.setenv("POLLINATE_BRAND_FILE", str(f))
    return f


def test_shipped_default_carries_no_brand(no_brand):
    sig = brand_config.signature()
    assert sig["watermark"] is None and sig["link"] is None and sig["link_text"] is None
    assert brand_config.qr_basenames(sig) == []
    assert not brand_config.load_brand()["brand"]["name"]


def test_user_file_merges_over_defaults(my_brand):
    brand = brand_config.load_brand()
    assert brand["brand"]["name"] == "Acme"
    assert brand["colors"]["primary"] == "#FF6B35"  # untouched default survives the merge
    sig = brand_config.signature()
    assert sig["link_text"] == "acme.example/blog"
    assert sig["qr"]["dark_on_light"] == str(my_brand.parent / "qr" / "acme-dark.png")
    assert sig["qr"]["light_on_dark"] is None
    assert brand_config.qr_basenames(sig) == ["acme-dark.png"]


@pytest.mark.parametrize("body", ["signature: {qr: just-a-string.png}", "signature: [", "- a list"])
def test_broken_brand_file_raises_a_brand_config_error(tmp_path, monkeypatch, body):
    f = tmp_path / "pollinate-brand.yaml"
    f.write_text(body)
    monkeypatch.setenv("POLLINATE_BRAND_FILE", str(f))
    with pytest.raises(brand_config.BrandConfigError):
        brand_config.signature()
