# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Hermes Profile Manager (onefile mode)

打包为单文件 exe：
  pyinstaller build.spec
输出：dist/HermesProfileManager.exe

注意：onefile 模式启动时会把依赖解压到 %TEMP%/_MEIxxxx/，启动比 onedir 慢 1-3 秒，
但分发更方便（只有一个文件）。如需更快启动，可改回 onedir 模式（见 git 历史）。
"""

import os

block_cipher = None
base_dir = os.path.abspath(".")

a = Analysis(
    ["main.py"],
    pathex=[base_dir],
    binaries=[],
    datas=[
        ("index.html", "."),
        ("static", "static"),
        ("icon.ico", "."),
        ("icon.png", "."),
        ("icon_256.png", "."),
    ],
    hiddenimports=[
        "flask",
        "flask_cors",
        "ruamel.yaml",
        "webview",
        "werkzeug.serving",
        # pywebview winforms 后端依赖链：pythonnet -> clr_loader -> cffi
        "cffi",
        "cffi._shim",
        "typing_extensions",
        "clr_loader",
        "clr_loader.ffi",
        "clr_loader.netfx",
        "clr_loader.coreclr",
        "clr_loader.hostfxr",
        "pythonnet",
        # .NET 运行时相关
        "pythonnet.runtime",
        "clr",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "test"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onefile 模式：所有依赖打包进单个 exe，运行时解压到临时目录
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="HermesProfileManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # 启用 onefile 模式
    onefile=True,
    # 启动时控制台隐藏（窗口应用）
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.ico",
)
