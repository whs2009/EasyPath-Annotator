# EasyPath Annotator · General edition

Create frame-specific point, polygon, tag and word annotations beside local whole-slide images. The native sidecars can be imported into EasyPath Server without converting the image or sending it to an external service.

## Downloads

**[v0.2.3-general.1](https://github.com/whs2009/EasyPath-Annotator/releases/tag/v0.2.3-general.1)**: all five General packages passed their [native build and packaged-app checks](https://github.com/whs2009/EasyPath-Annotator/actions/runs/45729156891). Use the direct downloads below and compare your file with [SHA256SUMS](https://github.com/whs2009/EasyPath-Annotator/releases/download/v0.2.3-general.1/SHA256SUMS). GitHub's automatic source ZIP is not a standalone executable.

| Platform | Release asset |
|---|---|
| Windows x86-64 | [EasyPath-Slide-Annotator-windows-x64.exe](https://github.com/whs2009/EasyPath-Annotator/releases/download/v0.2.3-general.1/EasyPath-Slide-Annotator-windows-x64.exe) |
| macOS Apple silicon | [EasyPath-Slide-Annotator-macos-arm64.dmg](https://github.com/whs2009/EasyPath-Annotator/releases/download/v0.2.3-general.1/EasyPath-Slide-Annotator-macos-arm64.dmg) |
| macOS Intel | [EasyPath-Slide-Annotator-macos-x64.dmg](https://github.com/whs2009/EasyPath-Annotator/releases/download/v0.2.3-general.1/EasyPath-Slide-Annotator-macos-x64.dmg) |
| Ubuntu x86-64 | [EasyPath-Slide-Annotator-ubuntu-x64.AppImage](https://github.com/whs2009/EasyPath-Annotator/releases/download/v0.2.3-general.1/EasyPath-Slide-Annotator-ubuntu-x64.AppImage) |
| Ubuntu ARM64 | [EasyPath-Slide-Annotator-ubuntu-arm64.AppImage](https://github.com/whs2009/EasyPath-Annotator/releases/download/v0.2.3-general.1/EasyPath-Slide-Annotator-ubuntu-arm64.AppImage) |

On Windows, compare `Get-FileHash .\<filename> -Algorithm SHA256` with the published checksum. On macOS use `shasum -a 256`, then open the DMG. On Ubuntu use `sha256sum` and give the AppImage execute permission before launching it. No Python installation is needed for packaged downloads. Follow your organization's signing and execution policies.

## Supported formats

| Input | General reader | What to check |
|---|---|---|
| EasyPath `.ep` | EP v1/v2 tiles; v3 focal stack | Z changes frame; pyramid levels change zoom. |
| Aperio `.svs`, Leica `.scn`, Ventana `.bif`, Sakura `.svslide` | OpenSlide | Must be a supported scanner encoding, not just a matching extension. |
| Hamamatsu `.ndpi` | OpenSlide; recognized focal stacks have a separate bounded reader | NDP.read is **not** included. Not all scanner variants are supported. |
| MIRAX `.mrxs` | OpenSlide | Keep the index file and its companion directory together. |
| Tiled `.tif` / `.tiff` | EasyPath transformed pyramids, supported focal TIFFs and recognized scanner WSI | Generic flat photographs and arbitrary TIFFs are not supported WSI. |
| DICOM WSI `.dcm` / `.dicom` | VL Whole Slide Microscopy Image Storage | Select an instance and keep its companion series available. CT and MR are not WSI. |
| Philips JPEG XL **DICOM WSI** | Standards-based DICOM + JPEG XL decoder, **not** PixelEngine | Supported unsigned three-channel display cases; JPEG XL transfer syntax UIDs ending in .110, .111 and .112. High-bit samples are mapped to RGB8 for display. Not every instrument, profile or series is qualified. |
| Philips native `.isyntax` | **Not included** | Distinct from Philips DICOM WSI. Requires a separately authorized reader. |

## Use the annotator

1. Open the slide from a writable folder. The app looks beside it for sidecars with the **entire filename, including extension**, as their prefix. For a stack, select the intended frame before editing.
2. **Ctrl+P** opens point free draw. Set the class, layer and radius. Choose **Change to circle** for a hollow point. Q pans, W adds, E removes; right-click a visible point to delete.
3. **Ctrl+G** opens polygon free draw. Hover over tissue, press **Shift+Z** and trace. **Shift+A** steps back, **Shift+R** clears the draft, **Shift+S** finishes and merges overlapping same-class/layer polygons, and **Shift+D** clips. Right-click a visible polygon to remove it. **Ctrl+Z/Y** undoes/redoes session edits.
4. Use **Ctrl+Alt+T** for tags and **Ctrl+O** for words. The layer panel controls visibility and colors. Switch slides with Page Up/Down and focal planes with Shift+Page Up/Down. The minimap, rotation, scale, macro preview, metadata, fullscreen and PNG/JPEG view export are available while annotating.
5. Completed edits are saved beside the slide. **Ctrl+S** confirms all completed annotations on the current slide across frames; it does not finish a polygon draft. For `sample.ndpi`, look for `sample.ndpi.points.json`, `sample.ndpi.polygons.geojson`, `sample.ndpi.tags.json` and `sample.ndpi.words.json`. Only edited types need files. Do not upload the editor lock or a screenshot in place of annotations.
6. In EasyPath Server, open the **same slide**, then **Annotation → Import annotations → EasyPath** (not Legacy EasyPath). Select the matching native companions, inspect counts, confirm, and verify alignment and frame assignment. Imported annotations are then available for permitted training, mapping/manipulation or review; import does not itself train a model or register a slide.

See the [full controls and shortcuts](tools/slide_viewer/ANNOTATOR-GUIDE.md).

## Scope and licensing

This General distribution contains **no Philips PixelEngine or Hamamatsu NDP.read SDK**. OpenSlide support for an NDPI is not a vendor license. For a restricted edition, first obtain vendor authorization covering the intended use and platform, then contact the EasyPath core team. Use public issues only for nonconfidential inquiries. Do not post slide images, barcodes, patient identifiers, license letters, credentials or annotation text.

The EasyPath-authored standalone source and examples in this repository are licensed under [Apache-2.0](LICENSE). Third-party components retain their own licenses and notices. The private EasyPath Server is not distributed or licensed by this repository. This software is not clinically validated.

## Build from source

Use a clean Python 3.12 environment on a supported native machine, a C++17 compiler and CMake. Install `tools/slide_viewer/requirements-build.txt`, then run `python packaging/build.py` from a fresh checkout (Linux: under Xvfb for GUI tests). The [release workflow](.github/workflows/general.yml) lists Linux build dependencies. The build downloads hash-checked FLTK, compiles the native UI, freezes the local reader/codec engine with its license notices and tests the packaged app with generated nonclinical data. No credentials or private server checkout are required to build.
