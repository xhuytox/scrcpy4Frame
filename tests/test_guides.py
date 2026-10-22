import pytest

from scrcpy4frame import guides
from scrcpy4frame import wizard as W

WIZARD_STEPS = [W.DEVOPTS, W.WIRELESS_ON, W.PICK_PHONE, W.PAIR_OPEN, W.PAIR_CODE, W.MANUAL,
                W.CONNECTING, W.OEM_INPUT, W.COMPANION, W.TILE, W.TILE_MANUAL, W.TEST,
                W.USB_SETUP, W.USB_PLUG, W.USB_AUTH, W.USB_PERMS, W.USB_PICK, "reenable"]


@pytest.mark.parametrize("brand", [b["id"] for b in guides.brands()])
@pytest.mark.parametrize("step", WIZARD_STEPS)
def test_every_step_has_resolved_text_for_every_brand(step, brand):
    g = guides.guide(step, brand, ssid="HomeNet")
    assert g["title"] and g["lines"]
    for text in [g["title"], g["tip"], *g["lines"], *g["help"]]:
        assert "{" not in text, (step, brand, text)


def test_brand_specific_paths():
    assert "Software information" in guides.guide(W.DEVOPTS, "samsung")["lines"][0]
    assert "Security settings" in " ".join(guides.guide(W.OEM_INPUT, "xiaomi")["lines"])
    assert "permission monitoring" in " ".join(guides.guide(W.OEM_INPUT, "oneplus")["lines"])
    assert '"HomeNet"' in guides.guide(W.WIRELESS_ON, "pixel", ssid="HomeNet")["lines"][0]
    # Step 3 is skipped when Wireless debugging is already on, and re-pairing starts here.
    assert "Additional settings" in guides.guide(W.PAIR_OPEN, "xiaomi")["lines"][0]
    assert "uaccess" in guides.guide(W.USB_PERMS, "pixel")["command"]


@pytest.mark.parametrize("brand", ["samsung", "pixel", "motorola"])
def test_plugging_in_says_to_let_the_device_control_usb(brand):
    # The Frame only becomes the USB host when the device asks for it, on every plug-in.
    lines = " ".join(guides.guide(W.USB_PLUG, brand)["lines"])
    assert 'Set "USB controlled by" to "This device".' in lines


@pytest.mark.parametrize("brand", ["xiaomi", "oneplus", "other"])
def test_unchecked_brands_get_softer_usb_control_wording(brand):
    # Not confirmed that these brands have the setting.
    lines = " ".join(guides.guide(W.USB_PLUG, brand)["lines"])
    assert 'If you see "USB controlled by", choose "This device".' in lines
    assert guides.text("usbControl", brand) == 'If you see "USB controlled by", choose "This device".'
