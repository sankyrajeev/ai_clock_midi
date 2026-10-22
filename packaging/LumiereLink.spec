# PyInstaller recipe for Lumière Link.app (run by packaging/build_mac.sh on a Mac).
from pathlib import Path

ROOT = Path(SPECPATH).parent
APP_NAME = "Lumiere Link"            # file name (plain ASCII is safest in paths)
DISPLAY_NAME = "Lumière Link"         # what Finder and the Dock show
VERSION = open(ROOT / "VERSION").read().strip()

a = Analysis(
    [str(ROOT / "lumiere_app.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "ui"), "ui"),
        (str(ROOT / "assets" / "brand"), "assets/brand"),
        (str(ROOT / "VERSION"), "."),
    ],
    hiddenimports=[
        "mido.backends.rtmidi", "rtmidi", "sounddevice", "numpy",
        "webview", "webview.platforms.cocoa",
        "phrases", "waveform", "sections", "pools", "projects", "lightkey", "netinfo", "simrig",
        "engine", "appserver", "prolink_sync",
    ],
    excludes=["tkinter", "pytest", "playwright"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=APP_NAME, console=False)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)
app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=str(ROOT / "build" / "AppIcon.icns"),
    bundle_identifier="com.vitalacoustica.lumierelink",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": DISPLAY_NAME,
        "CFBundleName": DISPLAY_NAME,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.music",
        "NSHumanReadableCopyright": "© Vital Acoustica",
        # The reasons macOS shows in its permission prompts:
        "NSLocalNetworkUsageDescription":
            "Lumière Link talks to your Pioneer CDJs and mixer over the DJ Link network.",
        "NSAppleEventsUsageDescription":
            "Lumière Link reads which project Lightkey has open, so it can use the matching Lumière project.",
        "NSMicrophoneUsageDescription":
            "The optional live audio backup listens to the FOH feed for tracks with no rekordbox data.",
    },
)
