# Build with: python -m PyInstaller --noconfirm --clean drop-once.spec
import sys

analysis = Analysis(
    ["desktop.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True,
          name="DropOnce", console=False, upx=False)
bundle = COLLECT(exe, analysis.binaries, analysis.datas, name="DropOnce", upx=False)
if sys.platform == "darwin":
    app = BUNDLE(bundle, name="DropOnce.app", icon=None,
                 bundle_identifier="io.github.yxzroot.droponce")
