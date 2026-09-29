# -*- mode: python -*-

block_cipher = None


a = Analysis(['aw_watcher_displays/__main__.py'],
             pathex=[],
             binaries=None,
             datas=None,
             hiddenimports=[
                'aw_watcher_displays.macos',
                'aw_watcher_displays.windows',
             ],
             hookspath=[],
             runtime_hooks=[],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher)
pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)
exe = EXE(pyz,
          a.scripts,
          exclude_binaries=True,
          name='aw-watcher-displays',
          contents_directory=".",
          debug=False,
          strip=False,
          upx=True,
          console=True )
coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               strip=False,
               upx=True,
               name='aw-watcher-displays')
