"""Preload venv-local CUDA libraries before torch is imported.

The Jetson torch 2.10 wheel needs libcudss.so.0. JetPack doesn't ship it, so it's
installed into the venv as the `nvidia-cudss-cu12` wheel (no sudo) and loaded here.
"""
import ctypes
import glob
import os
import sys


def preload():
    pattern = os.path.join(sys.prefix, "lib", "python3*", "site-packages", "nvidia", "cu12", "lib", "libcudss.so.0")
    for path in glob.glob(pattern):
        ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
        return path
    return None
