#!/usr/bin/env python3
"""Work out which CUDA runtime libraries CTranslate2 needs, and load them.

CTranslate2 (the engine behind faster-whisper) dlopen()s its CUDA libraries by
name at run time. Which ones depends on the build: every CUDA build needs
cuBLAS, older ones also need cuDNN. Rather than hard-coding versions, read the
library names out of the installed CTranslate2 binary.

The libraries are found if installed system-wide (Arch: `pacman -S cuda`), or,
when installed as pip wheels (nvidia-cublas-cu12, ...), once we load them into
the process ourselves, which is what `preload()` does.

CLI (used by setup.sh):
    python3 cuda_libs.py            # JSON report
    python3 cuda_libs.py --pip      # pip requirement strings for missing libs
"""
import ctypes
import glob
import importlib.util
import json
import os
import re
import sys

# pip distribution and import path that provide each library family
PIP_PACKAGES = {
    "cublas": ("nvidia-cublas-cu{major}", "nvidia.cublas"),
    "cudnn": ("nvidia-cudnn-cu{major}=={version}.*", "nvidia.cudnn"),
}


def _ctranslate2_binary():
    spec = importlib.util.find_spec("ctranslate2")
    if not spec or not spec.origin:
        return None
    site = os.path.dirname(os.path.dirname(spec.origin))
    candidates = glob.glob(os.path.join(site, "ctranslate2.libs", "libctranslate2*.so*"))
    candidates += glob.glob(os.path.join(os.path.dirname(spec.origin), "libctranslate2*.so*"))
    return max(candidates, key=os.path.getsize) if candidates else None


def required_libs():
    """Return {"cublas": "libcublas.so.12", "cudnn": "libcudnn.so.9"} as needed.

    Falls back to cuBLAS 12 only if the binary cannot be inspected.
    """
    needed = {}
    binary = _ctranslate2_binary()
    if binary:
        with open(binary, "rb") as f:
            data = f.read()
        for family in ("cublas", "cudnn"):
            names = set(re.findall(rb"lib%s\.so\.\d+" % family.encode(), data))
            if names:
                needed[family] = sorted(names)[-1].decode()
        if "cudnn" not in needed and b"cudnn" in data.lower():
            needed["cudnn"] = "libcudnn.so.9"
    if "cublas" not in needed:
        needed["cublas"] = "libcublas.so.12"
    return needed


def _wheel_dirs(import_path):
    try:
        spec = importlib.util.find_spec(import_path)
    except (ImportError, ValueError):
        return []
    if not spec or not spec.submodule_search_locations:
        return []
    return [os.path.join(p, "lib") for p in spec.submodule_search_locations]


def _load(soname, dirs):
    """dlopen a library globally, preferring pip wheel dirs. Returns error or None."""
    candidates = [os.path.join(d, soname) for d in dirs if os.path.exists(os.path.join(d, soname))]
    candidates.append(soname)  # system loader path
    error = None
    for candidate in candidates:
        try:
            ctypes.CDLL(candidate, mode=ctypes.RTLD_GLOBAL)
            return None
        except OSError as e:
            error = e
    return error


def preload():
    """Load everything CTranslate2 needs. Returns (ok, missing, pip_requirements)."""
    missing, pip = [], []
    for family, soname in required_libs().items():
        package, import_path = PIP_PACKAGES[family]
        dirs = _wheel_dirs(import_path)
        version = soname.rsplit(".", 1)[-1]

        if family == "cublas":
            # libcublas depends on libcublasLt from the same directory.
            _load(soname.replace("libcublas", "libcublasLt"), dirs)
        error = _load(soname, dirs)
        if error is not None:
            missing.append(soname)
            cuda_major = required_libs()["cublas"].rsplit(".", 1)[-1]
            pip.append(package.format(major=cuda_major, version=version))
            continue
        if family == "cudnn":
            # cuDNN is split into sub-libraries that are dlopen()ed lazily by name.
            for d in dirs:
                for path in sorted(glob.glob(os.path.join(d, f"libcudnn_*.so.{version}"))):
                    try:
                        ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
                    except OSError:
                        pass
    return not missing, missing, pip


def main():
    ok, missing, pip = preload()
    if "--pip" in sys.argv:
        print(" ".join(pip))
        return
    print(json.dumps({"required": required_libs(), "loadable": ok,
                      "missing": missing, "pip_install": pip}))


if __name__ == "__main__":
    main()
