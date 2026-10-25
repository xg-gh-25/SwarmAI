"""The macOS app is signed with the entitlements its Info.plist promises.

The bundle is signed with hardened runtime, so macOS only shows the
microphone prompt when the signature carries
``com.apple.security.device.audio-input``. Info.plist declaring
``NSMicrophoneUsageDescription`` is not enough: with the entitlement missing,
tccd logs "requires entitlement com.apple.security.device.audio-input but it
is missing" and refuses without asking, so voice input (and anything the app
spawns that records) can never get the microphone. The entitlements file only
reaches the signature through ``bundle.macOS.entitlements``.
"""
import json
import plistlib
from pathlib import Path

TAURI = Path(__file__).resolve().parents[2] / "desktop" / "src-tauri"


def _macos_bundle() -> dict:
    return json.loads((TAURI / "tauri.conf.json").read_text())["bundle"]["macOS"]


def test_signing_uses_the_entitlements_file():
    rel = _macos_bundle().get("entitlements")
    assert rel, "bundle.macOS.entitlements is not set: the app is signed with no entitlements"
    assert (TAURI / rel).is_file(), f"bundle.macOS.entitlements points at a missing file: {rel}"


def test_microphone_usage_comes_with_the_audio_input_entitlement():
    macos = _macos_bundle()
    info = plistlib.loads((TAURI / macos["infoPlist"]).read_bytes())
    if "NSMicrophoneUsageDescription" not in info:
        return
    ents = plistlib.loads((TAURI / macos["entitlements"]).read_bytes())
    assert ents.get("com.apple.security.device.audio-input") is True
