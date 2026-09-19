# Third-party notices

MaaBanG code is distributed under the MIT license in LICENSE.

The Windows package includes upstream runtime components and a patched UI:

- MaaFramework 5.12.2: https://github.com/MaaXYZ/MaaFramework (LGPL-3.0); source and license: https://github.com/MaaXYZ/MaaFramework/tree/v5.12.2
- MFAAvalonia 2.16.1: https://github.com/MaaXYZ/MFAAvalonia (GPL-3.0); corresponding source and license: https://github.com/MaaXYZ/MFAAvalonia/tree/v2.16.1
  MaaBanG modifies the normal startup path to perform startup device discovery in the background while preserving automatic connection. The source archive (including its GPL license), patch and build script are included in docs/upstream-ui/. This UI modification remains under GPL-3.0; the project's MIT license does not relicense it.
- CPython 3.12.10: https://www.python.org/downloads/release/python-31210/ (PSF license; included in python/LICENSE.txt).
- Python wheels and their dependencies retain their license files in python/Lib/site-packages, including MaaAgentBinary, NumPy, Pillow and StrEnum.
- OCR models come from the pinned MaaCommonAssets submodule: https://github.com/MaaXYZ/MaaCommonAssets/tree/232724783340245a93e304c4209a4d0f34e78c2f

Game screenshots are recognition templates from BanG Dream and remain the property of their respective rights holders. MaaBanG is an unofficial community project.

The MaaBanG icon uses Aya Maruyama character artwork as its reference, supplied from https://bestdori.com/assets/cn/characters/resourceset/res016005_rip/card_normal.png, with image-generated badge and wordmark composition. The character and source artwork remain the property of their respective rights holders.

Song metadata in agent/data/songs_cn.json and docs/data/songs_cn.csv comes from Bestdori (https://bestdori.com/info/songs), using its public songs/all.7.json and bands/all.1.json APIs. The snapshot records source URLs and retrieval time. This factual metadata and upstream game assets retain their respective rights; the project's MIT license does not relicense third-party content.
