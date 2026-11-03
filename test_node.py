"""Builds and runs the Lumière Node firmware's logic tests (firmware/lumiere-node/test)
with the computer's C++ compiler. The ESP32 build itself runs in GitHub Actions."""

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = Path(__file__).parent / "firmware" / "lumiere-node"


@pytest.mark.skipif(shutil.which("g++") is None, reason="needs a C++ compiler")
def test_node_logic(tmp_path):
    exe = tmp_path / "test_logic"
    subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(NODE / "src"),
                    str(NODE / "test" / "test_logic.cpp"), "-o", str(exe)], check=True)
    result = subprocess.run([str(exe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
