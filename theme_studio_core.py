from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable
import copy
import contextlib
import io
import json
import shutil
import struct
import sys
import urllib.request
import zipfile

from PIL import Image
from fontTools import subset, ttLib
from fontTools.ttLib.ttCollection import readTTCHeader
from pyfatfs import PyFatFS

from ipodhax.silverdb import pack_silverdb, unpack_silverdb


LogFn = Callable[[str], None]

if getattr(sys, "frozen", False):
    RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS"))
    APP_ROOT = Path(sys.executable).resolve().parent
else:
    RESOURCE_ROOT = Path(__file__).resolve().parent
    APP_ROOT = RESOURCE_ROOT

PROJECT_ROOT = APP_ROOT
STUDIO_ROOT = APP_ROOT / "studio_workspace"
CURRENT_ROOT = STUDIO_ROOT / "current"
SAVED_ROOT = STUDIO_ROOT / "saved_assets"
SAVED_METADATA_PATH = SAVED_ROOT / "metadata.json"
SESSION_PATH = STUDIO_ROOT / "session.json"

WORK_BODY = CURRENT_ROOT / "body"
WORK_EXPORTS = CURRENT_ROOT / "exports"
WORK_INPUTS = CURRENT_ROOT / "inputs"
WORK_OUTPUTS = CURRENT_ROOT / "outputs"
WORK_TMP = CURRENT_ROOT / "tmp"
WORK_FONTS = CURRENT_ROOT / "fonts"
WORK_INVENTORY = CURRENT_ROOT / "artwork_index.json"
WORK_FONT_INVENTORY = CURRENT_ROOT / "font_inventory.json"
WORK_FONT_REPLACEMENTS = CURRENT_ROOT / "font_replacements.json"
WORK_SILVER = WORK_EXPORTS / "SilverImagesDB.LE.bin"
WORK_SILVER_PACKED = WORK_EXPORTS / "SilverImagesDB.LE.bin2"
WORK_RSRC_BASE = WORK_TMP / "rsrc_base.bin"
WORK_RSRC_PATCHED = WORK_TMP / "rsrc_patched.bin"
WORK_MSE_BASE = WORK_TMP / "Firmware.MSE"


class StudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceProfile:
    key: str
    label: str
    family: str
    official_ipsw_url: str
    official_ipsw_name: str
    template_dir: str | None
    default_output_name: str


DEVICE_PROFILES = {
    "nano6": DeviceProfile(
        key="nano6",
        label="iPod nano 6",
        family="nano6",
        official_ipsw_url="http://appldnld.apple.com/iPod/SBML/osx/bundles/041-1920.20111004.CpeEw/iPod_1.2_36B10147.ipsw",
        official_ipsw_name="iPod_1.2_36B10147.ipsw",
        template_dir="iPod_1.2_36B10147",
        default_output_name="iPod_nano6_custom.ipsw",
    ),
    "nano7-2012": DeviceProfile(
        key="nano7-2012",
        label="iPod nano 7 (2012)",
        family="nano7-refresh",
        official_ipsw_url="http://appldnld.apple.com/ipod/sbml/osx/bundles/031-59796-20160525-8E6A5D46-21FF-11E6-89D1-C5D3662719FC/iPod_1.1.2_39A10023.ipsw",
        official_ipsw_name="iPod_1.1.2_39A10023.ipsw",
        template_dir="iPod_1.1.2_39A10023_2012",
        default_output_name="iPod_nano7_2012_custom.ipsw",
    ),
    "nano7-2015": DeviceProfile(
        key="nano7-2015",
        label="iPod nano 7 (2015)",
        family="nano7-refresh",
        official_ipsw_url="http://appldnld.apple.com/ipod/sbml/osx/bundles/031-59796-20160525-8E6A5D46-21FF-11E6-89D1-C5D3662719FC/iPod_1.1.2_39A10023.ipsw",
        official_ipsw_name="iPod_1.1.2_39A10023.ipsw",
        template_dir="iPod_1.1.2_39A10023_2015",
        default_output_name="iPod_nano7_2015_custom.ipsw",
    ),
}

ARTWORK_GROUPS = [
    {"key": "all", "label": "全部素材", "devices": None},
    {"key": "n6-icons", "label": "Nano 6 图标", "devices": {"nano6"}},
    {"key": "n6-wallpapers", "label": "Nano 6 壁纸（全部）", "devices": {"nano6"}},
    {"key": "n6-wallpapers-thumb", "label": "Nano 6 仅缩略图", "devices": {"nano6"}},
    {"key": "n7-icons", "label": "Nano 7 图标", "devices": {"nano7-2012", "nano7-2015"}},
    {"key": "n7-wallpapers", "label": "Nano 7 壁纸（全部）", "devices": {"nano7-2012", "nano7-2015"}},
    {"key": "n7-wallpapers-full", "label": "Nano 7 壁纸（240x432）", "devices": {"nano7-2012", "nano7-2015"}},
    {"key": "n7-wallpapers-thumb", "label": "Nano 7 壁纸缩略图（117x200）", "devices": {"nano7-2012", "nano7-2015"}},
]

SUPPORTED_TTC_CHILDREN = {
    "STHeiti-Medium.ttc": [
        {
            "member_index": 0,
            "member_name": "Heiti TC",
            "slot_id": "STHeiti-Medium.ttc::Heiti TC",
            "display_name": "STHeiti-Medium.ttc / Heiti TC",
            "hint": "繁体中文字体槽位",
        },
        {
            "member_index": 1,
            "member_name": "Heiti SC",
            "slot_id": "STHeiti-Medium.ttc::Heiti SC",
            "display_name": "STHeiti-Medium.ttc / Heiti SC",
            "hint": "简体中文主字体槽位",
        },
        {
            "member_index": 2,
            "member_name": "Heiti K",
            "slot_id": "STHeiti-Medium.ttc::Heiti K",
            "display_name": "STHeiti-Medium.ttc / Heiti K",
            "hint": "韩文相关字体槽位",
        },
        {
            "member_index": 3,
            "member_name": "Heiti J",
            "slot_id": "STHeiti-Medium.ttc::Heiti J",
            "display_name": "STHeiti-Medium.ttc / Heiti J",
            "hint": "日文相关字体槽位",
        },
    ]
}

SAFE_FONT_MODE = "safe"
EXPERIMENTAL_FONT_MODE = "experimental"
EXPERIMENTAL_HEITI_SC_SLOT = "STHeiti-Medium.ttc::Heiti SC"
HEITI_SC_SUBSET_PROFILE = "2000 常用简中字 + 假名 + Latin + 常用标点"


@dataclass
class SessionState:
    device_key: str = ""
    source_kind: str = ""
    source_label: str = ""
    source_ipsw_path: str = ""
    official_backup_path: str = ""
    imported_at: str = ""


@dataclass
class Section:
    tag: bytes
    name: bytes
    idk: int
    dev_offset: int
    length: int
    address: int
    entry_offset: int
    idk2: int
    version: int
    load_addr: int
    head: bytes
    body: bytes


@dataclass
class MseImage:
    header: bytes
    sections: list[Section]


@dataclass
class Img1Image:
    head: bytes
    body: bytes
    padding: bytes
    cert: bytes


def _mkdirs() -> None:
    for path in [STUDIO_ROOT, CURRENT_ROOT, SAVED_ROOT, WORK_BODY, WORK_EXPORTS, WORK_INPUTS, WORK_OUTPUTS, WORK_TMP, WORK_FONTS]:
        path.mkdir(parents=True, exist_ok=True)


def _reset_current_workspace() -> None:
    if CURRENT_ROOT.exists():
        shutil.rmtree(CURRENT_ROOT)
    _mkdirs()


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _unique_target_path(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate

    stem = Path(name).stem
    suffix = Path(name).suffix
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return directory / f"{stem}_{timestamp}{suffix}"


def _load_saved_metadata() -> dict[str, dict[str, str]]:
    if not SAVED_METADATA_PATH.exists():
        return {}
    try:
        return json.loads(SAVED_METADATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_saved_metadata(data: dict[str, dict[str, str]]) -> None:
    SAVED_ROOT.mkdir(parents=True, exist_ok=True)
    SAVED_METADATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_font_inventory() -> list[dict[str, object]]:
    if not WORK_FONT_INVENTORY.exists():
        return []
    try:
        data = json.loads(WORK_FONT_INVENTORY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save_font_inventory(items: list[dict[str, object]]) -> None:
    WORK_FONT_INVENTORY.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_font_replacements() -> dict[str, dict[str, object]]:
    if not WORK_FONT_REPLACEMENTS.exists():
        return {}
    try:
        data = json.loads(WORK_FONT_REPLACEMENTS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}

    normalized: dict[str, dict[str, object]] = {}
    for slot_name, entry in data.items():
        if isinstance(entry, str):
            normalized[slot_name] = {"source_path": entry, "staged_path": entry}
        elif isinstance(entry, dict):
            normalized_entry = dict(entry)
            source_path = str(normalized_entry.get("source_path", ""))
            staged_path = str(normalized_entry.get("staged_path", ""))
            if source_path or staged_path:
                normalized_entry["source_path"] = source_path
                normalized_entry["staged_path"] = staged_path
                if not normalized_entry.get("mode"):
                    normalized_entry["mode"] = SAFE_FONT_MODE
                normalized[slot_name] = normalized_entry
    return normalized


def _save_font_replacements(data: dict[str, dict[str, object]]) -> None:
    WORK_FONT_REPLACEMENTS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _scan_font_slots(rsrc_path: Path) -> list[dict[str, object]]:
    if not rsrc_path.exists():
        return []

    fat = PyFatFS.PyFatFS(str(rsrc_path), read_only=False)
    try:
        try:
            names = sorted(fat.listdir("/Resources/Fonts"))
        except Exception:
            return []
    finally:
        fat.close()

    items: list[dict[str, object]] = []
    for name in names:
        suffix = Path(name).suffix.lower()
        supported = suffix == ".ttf"
        hint = ""
        if name in {"Helvetica.ttf", "HelveticaBold.ttf"}:
            hint = "上游 README 明确说明过的常用槽位"
        items.append(
            {
                "slot_id": name,
                "name": name,
                "display_name": name,
                "extension": suffix or "",
                "supported": supported,
                "kind": "ttf-file" if supported else "file",
                "container_name": name,
                "member_index": None,
                "member_name": "",
                "hint": hint,
            }
        )
        for child in SUPPORTED_TTC_CHILDREN.get(name, []):
            items.append(
                {
                    "slot_id": child["slot_id"],
                    "name": child["slot_id"],
                    "display_name": child["display_name"],
                    "extension": suffix or "",
                    "supported": True,
                    "kind": "ttc-member",
                    "container_name": name,
                    "member_index": child["member_index"],
                    "member_name": child["member_name"],
                    "hint": child["hint"],
                }
            )
    return items


@lru_cache(maxsize=1)
def _default_heiti_sc_subset_text() -> str:
    chars = set(chr(codepoint) for codepoint in range(32, 127))
    chars.update(
        "，。、；：？！“”‘’（）【】《》〈〉「」『』—…·￥％＃＠＆＊"
        "＋＝－＿／＼|~`^<>"
    )
    for high in range(0xA1, 0xAA):
        for low in range(0xA1, 0xFF):
            try:
                chars.update(bytes([high, low]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    common_sc: list[str] = []
    for high in range(0xB0, 0xD8):
        for low in range(0xA1, 0xFF):
            try:
                decoded = bytes([high, low]).decode("gb2312")
            except UnicodeDecodeError:
                continue
            if "\u4e00" <= decoded <= "\u9fff":
                common_sc.append(decoded)
    chars.update(common_sc[:2000])
    chars.update(chr(codepoint) for codepoint in range(0x3040, 0x30FF + 1))
    chars.update(chr(codepoint) for codepoint in range(0x31F0, 0x31FF + 1))
    chars.update(chr(codepoint) for codepoint in range(0xFF66, 0xFF9D + 1))
    return "".join(sorted(chars))


def _vendor_id(font: ttLib.TTFont) -> str:
    if "OS/2" not in font:
        return ""
    value = getattr(font["OS/2"], "achVendID", "")
    if isinstance(value, bytes):
        return value.decode("ascii", "ignore").strip()
    return str(value).strip()


def _font_analysis(font: ttLib.TTFont, *, size_bytes: int = 0) -> dict[str, object]:
    family = ""
    if "name" in font:
        for name_id in (16, 1):
            with contextlib.suppress(Exception):
                family = font["name"].getDebugName(name_id) or ""
            if family:
                break
    return {
        "size_bytes": int(size_bytes),
        "glyph_count": int(getattr(font["maxp"], "numGlyphs", 0)) if "maxp" in font else 0,
        "units_per_em": int(getattr(font["head"], "unitsPerEm", 0)) if "head" in font else 0,
        "vendor_id": _vendor_id(font),
        "table_names": sorted(str(tag) for tag in font.keys()),
        "family_name": family,
    }


def _analyze_font_file(path: Path) -> dict[str, object]:
    font = ttLib.TTFont(str(path))
    try:
        return _font_analysis(font, size_bytes=path.stat().st_size)
    finally:
        font.close()


def _apply_target_font_identity(working_font: ttLib.TTFont, target_font: ttLib.TTFont) -> None:
    if "name" in target_font:
        working_font["name"] = copy.deepcopy(target_font["name"])
    if "OS/2" in working_font and "OS/2" in target_font:
        with contextlib.suppress(Exception):
            working_font["OS/2"].achVendID = copy.deepcopy(target_font["OS/2"].achVendID)


def _read_font_slot_bytes(rsrc_path: Path, slot: dict[str, object]) -> bytes:
    container_name = str(slot.get("container_name") or slot.get("name", ""))
    fs_path = f"/Resources/Fonts/{container_name}"
    fat = PyFatFS.PyFatFS(str(rsrc_path), read_only=False)
    try:
        with fat.openbin(fs_path, mode="rb") as stream:
            data = stream.read()
    finally:
        fat.close()

    if str(slot.get("kind", "file")) != "ttc-member":
        return data

    collection = ttLib.TTCollection(io.BytesIO(data))
    try:
        member_index = int(slot.get("member_index", 0))
        output = io.BytesIO()
        collection.fonts[member_index].save(output)
        return output.getvalue()
    finally:
        collection.close()


def _read_font_container_bytes(rsrc_path: Path, container_name: str) -> bytes:
    fs_path = f"/Resources/Fonts/{container_name}"
    fat = PyFatFS.PyFatFS(str(rsrc_path), read_only=False)
    try:
        with fat.openbin(fs_path, mode="rb") as stream:
            return stream.read()
    finally:
        fat.close()


def _ttc_member_window_bytes(ttc_bytes: bytes, member_index: int) -> int:
    header = readTTCHeader(io.BytesIO(ttc_bytes))
    offsets = sorted(int(offset) for offset in getattr(header, "offsetTable"))
    current_offset = int(getattr(header, "offsetTable")[member_index])
    next_offsets = [offset for offset in offsets if offset > current_offset]
    version = int.from_bytes(ttc_bytes[4:8], "big")
    if version == 0x00020000:
        dsig_offset = int.from_bytes(ttc_bytes[12 + len(offsets) * 4 + 8 : 12 + len(offsets) * 4 + 12], "big")
        if dsig_offset > current_offset:
            next_offsets.append(dsig_offset)
    current_limit = min(next_offsets) if next_offsets else len(ttc_bytes)
    return max(0, current_limit - current_offset)


def _replace_ttc_member_preserving_shell(ttc_bytes: bytes, member_index: int, replacement_font: ttLib.TTFont) -> bytes:
    header = readTTCHeader(io.BytesIO(ttc_bytes))
    offset_table = list(getattr(header, "offsetTable"))
    current_offset = int(offset_table[member_index])
    offset_entry_pos = 12 + member_index * 4

    size_probe = io.BytesIO()
    replacement_font._save(size_probe)
    replacement_size = len(size_probe.getvalue())

    version = int.from_bytes(ttc_bytes[4:8], "big")
    next_offsets = sorted(offset for offset in offset_table if offset > current_offset)
    if version == 0x00020000:
        dsig_offset = int.from_bytes(ttc_bytes[12 + len(offset_table) * 4 + 8 : 12 + len(offset_table) * 4 + 12], "big")
        if dsig_offset > current_offset:
            next_offsets.append(dsig_offset)
    current_limit = min(next_offsets) if next_offsets else len(ttc_bytes)
    if replacement_size <= current_limit - current_offset:
        buffer = io.BytesIO()
        buffer.write(ttc_bytes)
        buffer.seek(current_offset)
        replacement_font._save(buffer)
        return buffer.getvalue()

    buffer = io.BytesIO()
    buffer.write(ttc_bytes)
    while buffer.tell() % 4 != 0:
        buffer.write(b"\x00")

    new_member_offset = buffer.tell()
    buffer.seek(new_member_offset)
    replacement_font._save(buffer)
    output = bytearray(buffer.getvalue())
    struct.pack_into(">L", output, offset_entry_pos, new_member_offset)
    return bytes(output)


def _subset_font_to_text(source_path: Path, output_path: Path, text: str) -> None:
    options = subset.Options()
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.name_legacy = True
    options.layout_features = ["*"]
    options.notdef_glyph = True
    options.notdef_outline = True
    options.recommended_glyphs = True
    options.symbol_cmap = True

    font = subset.load_font(str(source_path), options)
    try:
        subsetter = subset.Subsetter(options=options)
        subsetter.populate(text=text)
        subsetter.subset(font)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output = io.BytesIO()
        subset.save_font(font, output, options)
        output_path.write_bytes(output.getvalue())
    finally:
        font.close()


def _build_font_import_report(
    slot: dict[str, object],
    source_info: dict[str, object],
    target_info: dict[str, object],
    staged_info: dict[str, object],
    *,
    mode: str,
    member_window_bytes: int | None = None,
) -> tuple[list[str], list[str], str]:
    notes: list[str] = []
    warnings: list[str] = []
    target_size = int(target_info.get("size_bytes", 0))
    staged_size = int(staged_info.get("size_bytes", 0))
    target_glyphs = int(target_info.get("glyph_count", 0))
    staged_glyphs = int(staged_info.get("glyph_count", 0))

    if mode == SAFE_FONT_MODE:
        notes.append("安全写入不会自动修复字体结构；推荐导入已用 FontForge 等工具处理过的字体。")
    else:
        notes.append(f"实验模式已按 {HEITI_SC_SUBSET_PROFILE} 自动裁剪字集，并继承 Heiti SC 槽位身份。")
        warnings.append("实验模式只针对 Heiti SC，不能保证任意中文 TTF 都能成功刷机。")

    if str(slot.get("kind", "")) == "ttc-member":
        warnings.append("TTC 成员写入会保留原 STHeiti-Medium.ttc 外壳，只替换当前 member。")
        if member_window_bytes is not None and staged_size > member_window_bytes:
            warnings.append(
                f"当前 member 在原 TTC 中只有 {_format_bytes(member_window_bytes)} 的独占空间；"
                "这个字体会迫使整个 TTC 变大，刷机失败风险较高。"
            )

    if mode == SAFE_FONT_MODE and str(slot.get("kind", "")) == "ttc-member":
        warnings.append("安全写入默认假设你导入的是已经手工处理好的字体。")

    if target_size and staged_size > target_size * 1.5:
        warnings.append("处理后的字体明显大于原槽位，刷机失败风险更高。")
    elif target_size and staged_size > target_size:
        warnings.append("处理后的字体大于原槽位，建议关注 rsrc 空间占用。")

    if target_glyphs and staged_glyphs and staged_glyphs < max(1500, target_glyphs // 5):
        warnings.append("当前字形覆盖明显少于原槽位，生僻字或媒体库标题可能缺字。")

    if target_info.get("vendor_id") and staged_info.get("vendor_id") != target_info.get("vendor_id"):
        warnings.append("字体厂商 ID 与目标槽位不同，兼容性可能较差。")

    risk_score = 0
    if mode == EXPERIMENTAL_FONT_MODE:
        risk_score += 1
    if member_window_bytes is not None and staged_size > member_window_bytes:
        risk_score += 3
    if target_size and staged_size > target_size:
        risk_score += 1
    if target_size and staged_size > target_size * 1.5:
        risk_score += 1
    if target_glyphs and staged_glyphs and staged_glyphs < max(1500, target_glyphs // 5):
        risk_score += 1
    if target_info.get("vendor_id") and staged_info.get("vendor_id") != target_info.get("vendor_id"):
        risk_score += 1

    risk_level = "高" if risk_score >= 3 else "中" if risk_score >= 1 else "低"
    return notes, warnings, risk_level


def _parse_artwork_filename(path: Path) -> tuple[str, str]:
    stem = path.stem
    parts = stem.split("_")
    if len(parts) >= 2 and parts[0].isdigit() and len(parts[1]) == 4 and parts[1].isdigit():
        return parts[0], parts[1]
    return "", ""


def _detect_saved_artwork_format(path: Path) -> str:
    try:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            colors = set(rgba.getdata())
    except OSError:
        return ""

    opaque_only = all(alpha == 255 for _red, _green, _blue, alpha in colors)
    rgb_colors = {(red, green, blue) for red, green, blue, _alpha in colors}
    grayscale = all(red == green == blue for red, green, blue in rgb_colors)
    color_count = len(colors)

    if opaque_only and grayscale:
        if color_count <= 16:
            return "0004"
        if color_count <= 256:
            return "0008"

    if color_count <= 255:
        return "0064"

    rgb565_like = all(
        red % 8 == 0 and green % 4 == 0 and blue % 8 == 0
        for red, green, blue in rgb_colors
    )
    if opaque_only and rgb565_like:
        return "0565"

    if color_count <= 65535:
        return "0065"
    return "1888"


def _patch_nano7_mse(mse_bytes: bytes) -> bytes:
    pattern = b"87402.0\x04"
    index = mse_bytes.find(pattern)
    if index == -1:
        raise StudioError("没有在 nano 7 的 Firmware.MSE 里找到预期的补丁签名。")
    patched = bytearray(mse_bytes)
    patched[index + len(pattern) - 1] = 0x03
    return bytes(patched)


NANO6_DISK_SWAP_PATCHES = (
    (0x5004, b"soso"),
    (0x5144, b"ksid"),
)


def _apply_nano6_disk_swap(mse_bytes: bytes) -> bytes:
    # Mirror the original CLI nano6 postprocess after rebuilding Firmware.MSE.
    patched = bytearray(mse_bytes)
    for offset, value in NANO6_DISK_SWAP_PATCHES:
        end = offset + len(value)
        if end > len(patched):
            raise StudioError("nano 6 Disk Swap patch exceeds Firmware.MSE length.")
        patched[offset:end] = value
    return bytes(patched)


def _verify_nano6_disk_swap(mse_bytes: bytes) -> None:
    for offset, value in NANO6_DISK_SWAP_PATCHES:
        end = offset + len(value)
        if mse_bytes[offset:end] != value:
            raise StudioError(
                f"nano 6 Disk Swap verification failed at 0x{offset:X}: expected {value!r}."
            )


def _parse_mse(mse_bytes: bytes, family: str) -> MseImage:
    header = mse_bytes[:0x5000]
    sections: list[Section] = []
    offset = 0x5000

    for _ in range(16):
        entry = mse_bytes[offset:offset + 40]
        offset += 40

        tag = entry[:4]
        if tag == b"\x00\x00\x00\x00":
            continue

        name = entry[4:8]
        idk = int.from_bytes(entry[8:12], "little")
        dev_offset = int.from_bytes(entry[12:16], "little")
        length = int.from_bytes(entry[16:20], "little")
        address = int.from_bytes(entry[20:24], "little")
        entry_offset = int.from_bytes(entry[24:28], "little")
        idk2 = int.from_bytes(entry[28:32], "little")
        version = int.from_bytes(entry[32:36], "little")
        load_addr = int.from_bytes(entry[36:40], "little")

        body_length = length + 0x800 if family == "nano6" else length
        head = mse_bytes[dev_offset:dev_offset + 0x1000]
        body = mse_bytes[dev_offset + 0x1000:dev_offset + 0x1000 + body_length]

        sections.append(
            Section(
                tag=tag,
                name=name,
                idk=idk,
                dev_offset=dev_offset,
                length=length,
                address=address,
                entry_offset=entry_offset,
                idk2=idk2,
                version=version,
                load_addr=load_addr,
                head=head,
                body=body,
            )
        )

    return MseImage(header=header, sections=sections)


def _build_mse(image: MseImage) -> bytes:
    out = bytearray()
    out.extend(image.header)

    for section in image.sections:
        out.extend(section.tag)
        out.extend(section.name)
        out.extend(section.idk.to_bytes(4, "little"))
        out.extend(section.dev_offset.to_bytes(4, "little"))
        out.extend(section.length.to_bytes(4, "little"))
        out.extend(section.address.to_bytes(4, "little"))
        out.extend(section.entry_offset.to_bytes(4, "little"))
        out.extend(section.idk2.to_bytes(4, "little"))
        out.extend(section.version.to_bytes(4, "little"))
        out.extend(section.load_addr.to_bytes(4, "little"))

    for _ in range(16 - len(image.sections)):
        out.extend(b"\x00" * 36)
        out.extend(b"\xFF" * 4)

    for section in sorted(image.sections, key=lambda item: item.dev_offset):
        while len(out) < section.dev_offset:
            out.append(0)
        out.extend(section.head)
        out.extend(section.body)

    while len(out) % 0x1000 != 0:
        out.append(0)

    return bytes(out)


def _parse_img1(data: bytes, family: str) -> Img1Image:
    body_length = int.from_bytes(data[12:16], "little")
    head = data[:0x54]
    padding = data[0x54:0x400]
    body = data[0x400:0x400 + body_length]
    cert = b"" if family == "nano6" else data[0x400 + body_length:]
    return Img1Image(head=head, body=body, padding=padding, cert=cert)


def _build_img1(image: Img1Image) -> bytes:
    return image.head + image.padding + image.body + image.cert


def _extract_silverdb(rsrc_path: Path, output_path: Path) -> None:
    fat = PyFatFS.PyFatFS(str(rsrc_path), read_only=False)
    try:
        with fat.openbin("/Resources/UI/SilverImagesDB.LE.bin", mode="rb") as stream:
            output_path.write_bytes(stream.read())
    finally:
        fat.close()


def _replace_silverdb(rsrc_path: Path, silver_path: Path) -> None:
    fat = PyFatFS.PyFatFS(str(rsrc_path), read_only=False)
    try:
        fs_path = "/Resources/UI/SilverImagesDB.LE.bin"
        fat.remove(fs_path)
        with fat.openbin(fs_path, mode="wb") as stream:
            stream.write(silver_path.read_bytes())
    finally:
        fat.close()


def _read_firmware_mse_from_archive(archive: zipfile.ZipFile) -> tuple[bytes, str] | tuple[None, None]:
    direct_matches = [
        name for name in archive.namelist()
        if Path(name).name.lower() == "firmware.mse"
    ]
    if not direct_matches:
        return None, None

    preferred = next((name for name in direct_matches if name == "Firmware.MSE"), direct_matches[0])
    return archive.read(preferred), preferred


def _load_community_ipsw_source(ipsw_path: Path, working_inputs_dir: Path) -> tuple[Path, bytes, str | None]:
    try:
        with zipfile.ZipFile(ipsw_path, "r") as archive:
            mse_bytes, mse_name = _read_firmware_mse_from_archive(archive)
            if mse_bytes is not None:
                source_copy = working_inputs_dir / ipsw_path.name
                _copy_file(ipsw_path, source_copy)
                note = None if mse_name == "Firmware.MSE" else f"已从包内路径 {mse_name} 读取 Firmware.MSE。"
                return source_copy, mse_bytes, note

            nested_candidates = [
                name for name in archive.namelist()
                if name.lower().endswith((".ipsw", ".zip"))
            ]
            for nested_name in nested_candidates:
                nested_bytes = archive.read(nested_name)
                try:
                    with zipfile.ZipFile(io.BytesIO(nested_bytes), "r") as nested_archive:
                        mse_bytes, _mse_name = _read_firmware_mse_from_archive(nested_archive)
                        if mse_bytes is None:
                            continue

                        nested_basename = Path(nested_name).name or ipsw_path.name
                        source_copy = working_inputs_dir / nested_basename
                        source_copy.write_bytes(nested_bytes)
                        note = f"检测到外层压缩包，已自动使用其中的 {nested_basename}。"
                        return source_copy, mse_bytes, note
                except zipfile.BadZipFile:
                    continue
    except zipfile.BadZipFile as exc:
        raise StudioError(
            "所选文件不是有效的 IPSW/ZIP。"
            " 如果这是社区作者额外压缩过的外层包，请先解压，再选择里面真正的 .ipsw 文件。"
        ) from exc

    raise StudioError(
        "所选 IPSW 中没有找到 Firmware.MSE。"
        " 如果你选中的是外层压缩包，请先解压后再选择其中真正的 IPSW 文件。"
    )


def _count_unique_colors(path: Path) -> int:
    with Image.open(path) as image:
        colors = set(image.convert("RGBA").getdata())
    return len(colors)


def _format_color_limit(image_format: str) -> int | None:
    if image_format == "0064":
        return 255
    if image_format == "0065":
        return 65535
    return None


def _format_bytes(size: int) -> str:
    return f"{size / (1024 * 1024):.2f} MB"


def _silverdb_write_budget(rsrc_path: Path, packed_size: int) -> dict[str, int] | None:
    if not rsrc_path.exists():
        return None

    fat = PyFatFS.PyFatFS(str(rsrc_path), read_only=False)
    try:
        fs = fat.fs
        free_clus = fs.FAT_CLUSTER_VALUES[fs.fat_type]["FREE_CLUSTER"]
        min_clus = fs.FAT_CLUSTER_VALUES[fs.fat_type]["MIN_DATA_CLUSTER"]
        max_clus = fs.FAT_CLUSTER_VALUES[fs.fat_type]["MAX_DATA_CLUSTER"]
        free_clusters = sum(
            1
            for index, value in enumerate(fs.fat)
            if min_clus <= index <= max_clus and value == free_clus
        )

        bytes_per_cluster = fs.bytes_per_cluster
        free_now = free_clusters * bytes_per_cluster

        current_size = 0
        try:
            current_size = fat.getinfo("/Resources/UI/SilverImagesDB.LE.bin", namespaces=["details"]).size
        except Exception:
            current_size = 0

        current_alloc = fs.calc_num_clusters(current_size) * bytes_per_cluster if current_size else 0
        required_alloc = fs.calc_num_clusters(packed_size) * bytes_per_cluster if packed_size else 0
        free_after_replace = free_now + current_alloc
        remaining = free_after_replace - required_alloc

        return {
            "bytes_per_cluster": bytes_per_cluster,
            "free_now": free_now,
            "current_size": current_size,
            "current_alloc": current_alloc,
            "required_alloc": required_alloc,
            "free_after_replace": free_after_replace,
            "remaining": remaining,
        }
    finally:
        fat.close()


class ThemeStudio:
    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = Path(project_root) if project_root else PROJECT_ROOT
        self.resource_root = RESOURCE_ROOT
        _mkdirs()

    def load_session(self) -> SessionState:
        if not SESSION_PATH.exists():
            return SessionState()
        return SessionState(**json.loads(SESSION_PATH.read_text(encoding="utf-8")))

    def save_session(self, session: SessionState) -> None:
        SESSION_PATH.write_text(json.dumps(asdict(session), indent=2, ensure_ascii=False), encoding="utf-8")

    def get_profile(self, device_key: str) -> DeviceProfile:
        try:
            return DEVICE_PROFILES[device_key]
        except KeyError as exc:
            raise StudioError(f"未知设备类型: {device_key}") from exc

    def download_official_backup(self, device_key: str, log: LogFn) -> Path:
        profile = self.get_profile(device_key)
        backup_path = WORK_INPUTS / profile.official_ipsw_name
        if backup_path.exists():
            log(f"官方固件备份已存在: {backup_path}")
            return backup_path

        log(f"下载官方固件: {profile.official_ipsw_url}")
        urllib.request.urlretrieve(profile.official_ipsw_url, backup_path)
        log(f"已保存官方固件备份: {backup_path}")
        return backup_path

    def import_official_firmware(self, device_key: str, log: LogFn) -> SessionState:
        profile = self.get_profile(device_key)
        _reset_current_workspace()
        backup_path = self.download_official_backup(device_key, log)

        with zipfile.ZipFile(backup_path, "r") as archive:
            try:
                mse_bytes = archive.read("Firmware.MSE")
            except KeyError as exc:
                raise StudioError("官方 IPSW 中没有找到 Firmware.MSE。") from exc

        if profile.family == "nano7-refresh":
            log("应用 nano 7 固件补丁。")
            mse_bytes = _patch_nano7_mse(mse_bytes)

        WORK_MSE_BASE.write_bytes(mse_bytes)
        self._prepare_artwork_workspace(profile.family, mse_bytes, log)

        session = SessionState(
            device_key=device_key,
            source_kind="official",
            source_label=f"{profile.label} 官方固件",
            source_ipsw_path=str(backup_path),
            official_backup_path=str(backup_path),
            imported_at=datetime.now().isoformat(timespec="seconds"),
        )
        self.save_session(session)
        log("官方固件已就绪，可以开始浏览和替换素材。")
        return session

    def import_community_ipsw(self, device_key: str, ipsw_path: Path, log: LogFn) -> SessionState:
        profile = self.get_profile(device_key)
        _reset_current_workspace()
        source_copy, mse_bytes, import_note = _load_community_ipsw_source(ipsw_path, WORK_INPUTS)
        log(f"已复制社区 IPSW 到工作区: {source_copy.name}")
        if import_note:
            log(import_note)

        WORK_MSE_BASE.write_bytes(mse_bytes)
        self._prepare_artwork_workspace(profile.family, mse_bytes, log)

        session = SessionState(
            device_key=device_key,
            source_kind="community",
            source_label=f"{profile.label} 社区 IPSW",
            source_ipsw_path=str(source_copy),
            imported_at=datetime.now().isoformat(timespec="seconds"),
        )
        self.save_session(session)
        log("社区 IPSW 已导入，可以开始浏览和替换素材。")
        return session

    def _prepare_artwork_workspace(self, family: str, mse_bytes: bytes, log: LogFn) -> None:
        mse = _parse_mse(mse_bytes, family)
        section = next((item for item in mse.sections if item.name == b"crsr"), None)
        if section is None:
            raise StudioError("Firmware.MSE 中没有找到 rsrc/crsr 分区。")

        img1 = _parse_img1(section.body, family)
        WORK_RSRC_BASE.write_bytes(img1.body)
        _extract_silverdb(WORK_RSRC_BASE, WORK_SILVER)
        log("已导出 SilverImagesDB.LE.bin。")

        if WORK_BODY.exists():
            shutil.rmtree(WORK_BODY)
        WORK_BODY.mkdir(parents=True, exist_ok=True)

        with contextlib.redirect_stdout(io.StringIO()):
            with WORK_SILVER.open("rb") as stream:
                unpack_silverdb(stream, WORK_BODY)
        inventory = self._scan_body_items()
        WORK_INVENTORY.write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding="utf-8")
        if WORK_FONTS.exists():
            shutil.rmtree(WORK_FONTS)
        WORK_FONTS.mkdir(parents=True, exist_ok=True)
        _save_font_inventory(_scan_font_slots(WORK_RSRC_BASE))
        _save_font_replacements({})
        log("已解包美术资源到 body 目录。")

    def _scan_body_items(self) -> list[dict[str, str]]:
        if not WORK_BODY.exists():
            return []

        items = []
        for path in sorted(WORK_BODY.glob("*_*.png"), key=lambda item: int(item.stem.split("_")[0])):
            with Image.open(path) as image:
                width, height = image.size
            image_id, image_format = path.stem.split("_")
            items.append(
                {
                    "id": image_id,
                    "format": image_format,
                    "size": f"{width}x{height}",
                    "width": str(width),
                    "height": str(height),
                    "name": path.name,
                    "path": str(path),
                }
            )
        return items

    def _load_inventory(self) -> list[dict[str, str]]:
        if not WORK_INVENTORY.exists():
            return []
        return json.loads(WORK_INVENTORY.read_text(encoding="utf-8"))

    def fonts_dir(self) -> Path:
        return WORK_FONTS

    def list_fonts(self) -> list[dict[str, object]]:
        inventory = _load_font_inventory()
        needs_rescan = False
        if inventory:
            if any("slot_id" not in item for item in inventory):
                needs_rescan = True
            else:
                inventory_names = {str(item.get("container_name", "")) for item in inventory} | {
                    str(item.get("name", "")) for item in inventory
                }
                for container_name, children in SUPPORTED_TTC_CHILDREN.items():
                    if container_name not in inventory_names:
                        continue
                    child_slot_ids = {str(item.get("slot_id", "")) for item in inventory}
                    if not all(child["slot_id"] in child_slot_ids for child in children):
                        needs_rescan = True
                        break
        if (not inventory or needs_rescan) and WORK_RSRC_BASE.exists():
            inventory = _scan_font_slots(WORK_RSRC_BASE)
            _save_font_inventory(inventory)

        replacements = _load_font_replacements()
        items: list[dict[str, object]] = []
        for item in inventory:
            slot_name = str(item.get("slot_id", item.get("name", "")))
            replacement = replacements.get(slot_name, {})
            replacement_path = replacement.get("source_path", "") or replacement.get("staged_path", "")
            supported = bool(item.get("supported"))
            if replacement_path and supported:
                status = "已指定替换"
            elif supported:
                status = "原始"
            else:
                status = "暂不支持"
            items.append(
                {
                    "name": slot_name,
                    "display_name": str(item.get("display_name", slot_name)),
                    "extension": str(item.get("extension", "")),
                    "supported": supported,
                    "status": status,
                    "replacement_path": replacement_path,
                    "kind": str(item.get("kind", "file")),
                    "container_name": str(item.get("container_name", "")),
                    "member_index": item.get("member_index"),
                    "member_name": str(item.get("member_name", "")),
                    "hint": str(item.get("hint", "")),
                    "replacement_info": replacement,
                }
            )
        return items

    def stage_font_replacement(self, slot_name: str, source_path: Path, mode: str = SAFE_FONT_MODE) -> Path:
        slot = next((item for item in self.list_fonts() if item["name"] == slot_name), None)
        if slot is None:
            raise StudioError(f"没有找到字体槽位：{slot_name}")
        if not bool(slot["supported"]):
            raise StudioError(f"{slot_name} 当前不是可替换的 .ttf 槽位")
        if source_path.suffix.lower() != ".ttf":
            raise StudioError("v1 只支持导入 .ttf 字体文件")
        if not source_path.exists():
            raise StudioError(f"没有找到要导入的字体文件：{source_path}")
        try:
            test_font = ttLib.TTFont(str(source_path))
            test_font.close()
        except Exception as exc:
            raise StudioError("选中的字体文件不是可用的 TrueType 字体，无法导入。") from exc

        WORK_FONTS.mkdir(parents=True, exist_ok=True)
        safe_name = slot_name.replace("::", "__").replace("/", "_")
        staged_path = WORK_FONTS / safe_name
        _copy_file(source_path, staged_path)

        replacements = _load_font_replacements()
        replacements[slot_name] = {
            "source_path": str(source_path.resolve()),
            "staged_path": str(staged_path.resolve()),
        }
        _save_font_replacements(replacements)
        return staged_path

    def clear_font_replacement(self, slot_name: str) -> None:
        replacements = _load_font_replacements()
        entry = replacements.pop(slot_name, None)
        if entry:
            staged_path = Path(entry.get("staged_path", ""))
            if staged_path.exists():
                staged_path.unlink()
        _save_font_replacements(replacements)

    def export_current_font(self, slot_name: str, output_path: Path) -> Path:
        if not WORK_RSRC_BASE.exists():
            raise StudioError("当前还没有可导出的字体工作区，请先导入固件或 IPSW")

        slot = next((item for item in self.list_fonts() if item["name"] == slot_name), None)
        if slot is None:
            raise StudioError(f"没有找到字体槽位: {slot_name}")
        container_name = str(slot.get("container_name") or slot_name)
        fs_path = f"/Resources/Fonts/{container_name}"
        fat = PyFatFS.PyFatFS(str(WORK_RSRC_BASE), read_only=False)
        try:
            with fat.openbin(fs_path, mode="rb") as stream:
                data = stream.read()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if slot.get("kind") == "ttc-member":
                collection = ttLib.TTCollection(io.BytesIO(data))
                member_index = int(slot.get("member_index", 0))
                collection.fonts[member_index].save(str(output_path))
                collection.close()
            else:
                output_path.write_bytes(data)
        except Exception as exc:
            raise StudioError(f"导出字体失败：{slot_name}") from exc
        finally:
            fat.close()
        return output_path

    def apply_font_replacements(self, rsrc_path: Path, log: LogFn) -> list[str]:
        replacements = _load_font_replacements()
        if not replacements:
            return []

        font_items = {str(item["name"]): item for item in self.list_fonts()}
        fat = PyFatFS.PyFatFS(str(rsrc_path), read_only=False)
        applied: list[str] = []
        try:
            for slot_name, entry in replacements.items():
                slot = font_items.get(slot_name)
                if not slot or not bool(slot.get("supported")):
                    log(f"字体槽位暂不支持替换，已跳过：{slot_name}")
                    continue

                staged_path = Path(entry.get("staged_path", ""))
                if not staged_path.exists():
                    log(f"没有找到已暂存的替换字体，已跳过：{slot_name}")
                    continue

                slot_kind = str(slot.get("kind", "file"))
                container_name = str(slot.get("container_name") or slot_name)
                fs_path = f"/Resources/Fonts/{container_name}"
                if slot_kind == "ttc-member":
                    collection = None
                    replacement_font = None
                    try:
                        with fat.openbin(fs_path, mode="rb") as stream:
                            collection = ttLib.TTCollection(io.BytesIO(stream.read()))
                        member_index = int(slot.get("member_index", 0))
                        original_font = collection.fonts[member_index]
                        replacement_font = ttLib.TTFont(str(staged_path))
                        replacement_font["name"] = copy.deepcopy(original_font["name"])
                        collection.fonts[member_index] = replacement_font

                        output = io.BytesIO()
                        collection.save(output)

                        fat.remove(fs_path)
                        with fat.openbin(fs_path, mode="wb") as stream:
                            stream.write(output.getvalue())
                        applied.append(slot_name)
                        log(f"已应用字体替换：{slot_name}")
                    except Exception as exc:
                        raise StudioError(f"写回字体槽位失败：{slot_name} ({exc})") from exc
                    finally:
                        if replacement_font is not None:
                            replacement_font.close()
                        if collection is not None:
                            collection.close()
                    continue

                original_font = None
                replacement_font = None
                try:
                    with fat.openbin(fs_path, mode="rb") as stream:
                        original_font = ttLib.TTFont(io.BytesIO(stream.read()))
                    replacement_font = ttLib.TTFont(str(staged_path))
                    replacement_font["name"] = copy.deepcopy(original_font["name"])

                    output = io.BytesIO()
                    replacement_font.save(output)

                    fat.remove(fs_path)
                    with fat.openbin(fs_path, mode="wb") as stream:
                        stream.write(output.getvalue())
                    applied.append(slot_name)
                    log(f"已应用字体替换：{slot_name}")
                except Exception as exc:
                    raise StudioError(f"写回字体槽位失败：{slot_name} ({exc})") from exc
                finally:
                    if replacement_font is not None:
                        replacement_font.close()
                    if original_font is not None:
                        original_font.close()
        finally:
            fat.close()

        return applied

    def get_artwork_groups(self, device_key: str | None = None) -> list[dict[str, str]]:
        resolved_device = device_key or self.load_session().device_key
        groups = []
        for group in ARTWORK_GROUPS:
            if group["devices"] is None or resolved_device in group["devices"]:
                groups.append({"key": group["key"], "label": group["label"]})
        return groups

    def _item_matches_group(self, device_key: str, item: dict[str, str], group_key: str) -> bool:
        if group_key == "all":
            return True

        item_id = int(item["id"])
        size = item["size"]

        if device_key == "nano6":
            if group_key == "n6-icons":
                return 229442241 <= item_id <= 229442314
            if group_key == "n6-wallpapers":
                return 229442315 <= item_id <= 229442371
            if group_key == "n6-wallpapers-thumb":
                return 229442338 <= item_id <= 229442352 or (
                    229442353 <= item_id <= 229442371 and item_id % 2 == 1
                )

        if device_key in {"nano7-2012", "nano7-2015"}:
            if group_key == "n7-icons":
                return 229442200 <= item_id <= 229442211
            if group_key == "n7-wallpapers":
                return 229442215 <= item_id <= 229442323
            if group_key == "n7-wallpapers-full":
                return 229442215 <= item_id <= 229442323 and size == "240x432"
            if group_key == "n7-wallpapers-thumb":
                return 229442215 <= item_id <= 229442323 and size == "117x200"

        return False

    def describe_artwork_group(self, item: dict[str, str], device_key: str | None = None) -> str:
        resolved_device = device_key or self.load_session().device_key
        if resolved_device == "nano6":
            if self._item_matches_group(resolved_device, item, "n6-icons"):
                return "Nano 6 图标"
            item_id = int(item["id"])
            if 229442315 <= item_id <= 229442337 or (229442353 <= item_id <= 229442371 and item_id % 2 == 0):
                return "Nano 6 壁纸"
            if self._item_matches_group(resolved_device, item, "n6-wallpapers-thumb"):
                return "Nano 6 壁纸缩略图"
            if self._item_matches_group(resolved_device, item, "n6-wallpapers"):
                return "Nano 6 壁纸相关"
        if resolved_device in {"nano7-2012", "nano7-2015"}:
            if self._item_matches_group(resolved_device, item, "n7-icons"):
                return "Nano 7 图标"
            if self._item_matches_group(resolved_device, item, "n7-wallpapers-full"):
                return "Nano 7 壁纸"
            if self._item_matches_group(resolved_device, item, "n7-wallpapers-thumb"):
                return "Nano 7 壁纸缩略图"
            if self._item_matches_group(resolved_device, item, "n7-wallpapers"):
                return "Nano 7 壁纸相关"
        return "未分组"

    def list_artwork(self, group_key: str = "all") -> list[dict[str, str]]:
        items = self._scan_body_items()
        device_key = self.load_session().device_key
        if not device_key:
            return items

        filtered = [item for item in items if self._item_matches_group(device_key, item, group_key)]
        for item in filtered:
            item["group"] = self.describe_artwork_group(item, device_key)
        return filtered

    def capacity_summary(self, packed_size: int | None = None) -> str:
        inventory = self._load_inventory()
        current_items = self._scan_body_items()
        budget = _silverdb_write_budget(WORK_RSRC_BASE, packed_size) if packed_size is not None else None
        if not inventory:
            if packed_size is None:
                return "容量提醒：重新导入一次官方固件或社区 IPSW 后，才能更准确地追踪哪些素材被升级成了 1888。"

            original_size = WORK_SILVER.stat().st_size if WORK_SILVER.exists() else 0
            delta = packed_size - original_size
            budget_note = ""
            if budget:
                remaining = budget["remaining"]
                if remaining < 0:
                    budget_note = f" 按当前 rsrc 分区容量估算，还差 {_format_bytes(-remaining)}，实际写回会失败。"
                else:
                    budget_note = f" 按当前 rsrc 分区容量估算，写回后还剩 {_format_bytes(remaining)}。"
            return (
                f"原始 SilverImagesDB 为 {_format_bytes(original_size)}，"
                f"当前为 {_format_bytes(packed_size)}，变化 {delta / (1024 * 1024):+.2f} MB。"
                f"由于当前工作区缺少初始素材清单，暂时无法统计 1888 升格数量。{budget_note}"
            )

        inventory_map = {item["id"]: item for item in inventory}
        promoted = [
            item for item in current_items
            if item["format"] == "1888" and inventory_map.get(item["id"], {}).get("format") != "1888"
        ]

        if packed_size is None:
            if not promoted:
                return "容量提醒：目前没有检测到从低色深自动升到 1888 的素材。"
            sample = "、".join(item["name"] for item in promoted[:3])
            if len(promoted) > 3:
                sample += " 等"
            return (
                f"容量提醒：已检测到 {len(promoted)} 个素材升格为 1888，"
                f"例如 {sample}。1888 越多、尺寸越大，越容易让 rsrc 分区超限。"
            )

        original_size = WORK_SILVER.stat().st_size if WORK_SILVER.exists() else 0
        delta = packed_size - original_size
        if budget and budget["remaining"] < 0:
            risk = (
                f"按当前 rsrc 分区容量估算，写回新的 SilverImagesDB 后还差 {_format_bytes(-budget['remaining'])}，"
                "实际写回会失败。"
            )
        elif budget and budget["remaining"] < 512 * 1024:
            risk = f"按当前 rsrc 分区容量估算，写回后只剩 {_format_bytes(budget['remaining'])}，已经非常接近上限。"
        elif delta <= 0:
            risk = "当前打包后的 SilverImagesDB 没有比原始包更大。"
        elif delta < 512 * 1024:
            risk = "当前增量较小，但仍建议实际刷机前保留原始备份。"
        elif delta < 2 * 1024 * 1024:
            risk = "当前增量已经比较明显，若还要继续把更多素材转成 1888，需要留意超限风险。"
        else:
            risk = "当前增量较大，已经属于高风险体积增长，建议减少 1888 大图替换。"

        promoted_suffix = f" 已升格到 1888 的素材数：{len(promoted)}。" if promoted else ""
        budget_suffix = ""
        if budget:
            budget_suffix = (
                f" rsrc 分区可用预算：写回时可用 {_format_bytes(budget['free_after_replace'])}，"
                f"当前需要 {_format_bytes(budget['required_alloc'])}，"
                f"余量 {budget['remaining'] / (1024 * 1024):+.2f} MB。"
            )
        return (
            f"原始 SilverImagesDB 为 {_format_bytes(original_size)}，"
            f"当前为 {_format_bytes(packed_size)}，变化 {delta / (1024 * 1024):+.2f} MB。"
            f"{risk}{promoted_suffix}{budget_suffix}"
        )

    def validate_replacement(self, target_name: str, candidate_path: Path) -> tuple[str, list[str]]:
        target_path = WORK_BODY / target_name
        if not target_path.exists():
            raise StudioError(f"目标素材不存在: {target_name}")

        notes: list[str] = []

        with Image.open(target_path) as target_image, Image.open(candidate_path) as candidate_image:
            if target_image.size != candidate_image.size:
                raise StudioError(
                    f"尺寸不匹配: 原图 {target_image.size[0]}x{target_image.size[1]}，"
                    f"替换图 {candidate_image.size[0]}x{candidate_image.size[1]}"
                )

        image_id, image_format = target_path.stem.split("_")
        output_format = image_format
        color_limit = _format_color_limit(image_format)

        if color_limit is not None:
            color_count = _count_unique_colors(candidate_path)
            if color_count > color_limit:
                output_format = "1888"
                notes.append(
                    f"原格式 {image_format} 颜色上限为 {color_limit}，当前图片有 {color_count} 色，已自动改为 1888。"
                )
            else:
                notes.append(f"调色板颜色数: {color_count}/{color_limit}")
        elif image_format in {"0004", "0008"}:
            notes.append("灰度格式素材，打包时会自动转换为灰度。")
        elif image_format == "0565":
            notes.append("RGB565 素材，打包时会自动转换为 16 位色。")
        else:
            notes.append("RGBA 素材，可直接使用。")

        return f"{image_id}_{output_format}.png", notes

    def replace_artwork(self, target_name: str, candidate_path: Path) -> tuple[str, list[str]]:
        output_name, notes = self.validate_replacement(target_name, candidate_path)
        image_id = target_name.split("_", 1)[0]

        for existing_path in WORK_BODY.glob(f"{image_id}_*.png"):
            existing_path.unlink()

        shutil.copy2(candidate_path, WORK_BODY / output_name)
        if output_name != target_name:
            notes.append(f"已按同素材 ID 自动重命名为 {output_name}")
        return output_name, notes

    def replace_artwork_with_format(
        self,
        target_name: str,
        candidate_path: Path,
        output_format: str,
    ) -> tuple[str, list[str]]:
        target_path = WORK_BODY / target_name
        if not target_path.exists():
            raise StudioError(f"目标素材不存在: {target_name}")

        with Image.open(target_path) as target_image, Image.open(candidate_path) as candidate_image:
            if target_image.size != candidate_image.size:
                raise StudioError(
                    f"尺寸不匹配: 原图 {target_image.size[0]}x{target_image.size[1]}，"
                    f"替换图 {candidate_image.size[0]}x{candidate_image.size[1]}"
                )

        image_id = target_name.split("_", 1)[0]
        for existing_path in WORK_BODY.glob(f"{image_id}_*.png"):
            existing_path.unlink()

        output_name = f"{image_id}_{output_format}.png"
        shutil.copy2(candidate_path, WORK_BODY / output_name)
        return output_name, [f"已手动按 {output_format} 格式写回当前素材。"]

    def save_artwork_copy(self, asset_name: str, note: str = "") -> Path:
        source_path = WORK_BODY / asset_name
        if not source_path.exists():
            raise StudioError(f"要保存的素材不存在: {asset_name}")

        target_path = _unique_target_path(SAVED_ROOT, source_path.name)
        _copy_file(source_path, target_path)
        self.update_saved_artwork_note(target_path, note)
        return target_path

    def import_saved_asset(self, source_path: Path, note: str = "", preferred_name: str | None = None) -> Path:
        if not source_path.exists():
            raise StudioError(f"要导入的素材不存在: {source_path}")

        target_name = preferred_name or source_path.name
        target_path = _unique_target_path(SAVED_ROOT, target_name)
        _copy_file(source_path, target_path)
        self.update_saved_artwork_note(target_path, note)
        return target_path

    def replace_saved_artwork(
        self,
        asset_path: Path,
        candidate_path: Path,
        output_name: str | None = None,
    ) -> Path:
        if not asset_path.exists():
            raise StudioError(f"要更新的收藏素材不存在: {asset_path}")
        if not candidate_path.exists():
            raise StudioError(f"新的收藏素材不存在: {candidate_path}")

        target_name = output_name or asset_path.name
        target_path = SAVED_ROOT / target_name

        metadata = _load_saved_metadata()
        entry = metadata.pop(asset_path.name, {})

        if target_path != asset_path and target_path.exists():
            target_path.unlink()

        _copy_file(candidate_path, target_path)

        if target_path != asset_path and asset_path.exists():
            asset_path.unlink()

        if entry:
            entry["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            metadata[target_path.name] = entry

        _save_saved_metadata(metadata)
        return target_path

    def delete_saved_artwork(self, asset_path: Path) -> None:
        if asset_path.exists():
            asset_path.unlink()

        metadata = _load_saved_metadata()
        if asset_path.name in metadata:
            del metadata[asset_path.name]
            _save_saved_metadata(metadata)

    def update_saved_artwork_note(self, asset_path: Path, note: str) -> None:
        metadata = _load_saved_metadata()
        entry = metadata.get(asset_path.name, {})
        entry["note"] = note.strip()
        entry["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        metadata[asset_path.name] = entry
        _save_saved_metadata(metadata)

    def list_saved_artwork(self, search_text: str = "") -> list[dict[str, str]]:
        if not SAVED_ROOT.exists():
            return []

        metadata = _load_saved_metadata()
        allowed = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        query = search_text.strip().lower()
        items: list[dict[str, str]] = []
        for path in sorted(
            [item for item in SAVED_ROOT.iterdir() if item.is_file() and item.suffix.lower() in allowed],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            try:
                with Image.open(path) as image:
                    size = f"{image.size[0]}x{image.size[1]}"
            except OSError:
                size = "?"

            image_id, image_format = _parse_artwork_filename(path)
            if not image_format:
                image_format = _detect_saved_artwork_format(path)
            note = metadata.get(path.name, {}).get("note", "")
            if query and query not in path.name.lower() and query not in note.lower():
                continue
            items.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "id": image_id,
                    "format": image_format,
                    "size": size,
                    "note": note,
                    "saved_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                }
            )
        return items

    def repack_silverdb(self, log: LogFn) -> Path:
        if not WORK_BODY.exists():
            raise StudioError("还没有可打包的 body 目录。")

        with contextlib.redirect_stdout(io.StringIO()):
            with WORK_SILVER_PACKED.open("wb") as stream:
                pack_silverdb(stream, WORK_BODY)
        log(f"已生成 {WORK_SILVER_PACKED.name}")
        return WORK_SILVER_PACKED

    def estimate_packed_silverdb_size(self) -> int:
        if not WORK_BODY.exists():
            raise StudioError("还没有可打包的 body 目录。")

        with contextlib.redirect_stdout(io.StringIO()):
            stream = io.BytesIO()
            pack_silverdb(stream, WORK_BODY)
        return len(stream.getbuffer())

    def build_ipsw(self, output_path: Path, log: LogFn) -> Path:
        session = self.load_session()
        if not session.device_key or not session.source_kind:
            raise StudioError("请先导入官方固件或社区 IPSW。")

        profile = self.get_profile(session.device_key)
        silver_path = self.repack_silverdb(log)
        log(self.capacity_summary(silver_path.stat().st_size))
        _copy_file(WORK_RSRC_BASE, WORK_RSRC_PATCHED)
        _replace_silverdb(WORK_RSRC_PATCHED, silver_path)
        applied_fonts = self.apply_font_replacements(WORK_RSRC_PATCHED, log)
        if applied_fonts:
            log(f"字体替换只会在最终打包时写入，本次已应用 {len(applied_fonts)} 个字体槽位。")
        log("已把新的 SilverImagesDB 写回 rsrc 分区。")

        mse = _parse_mse(WORK_MSE_BASE.read_bytes(), profile.family)
        section = next((item for item in mse.sections if item.name == b"crsr"), None)
        if section is None:
            raise StudioError("回写时没有找到 rsrc/crsr 分区。")

        img1 = _parse_img1(section.body, profile.family)
        img1.body = WORK_RSRC_PATCHED.read_bytes()
        section.body = _build_img1(img1)
        new_mse = _build_mse(mse)
        if profile.family == "nano6":
            new_mse = _apply_nano6_disk_swap(new_mse)
            _verify_nano6_disk_swap(new_mse)
            log("已应用 nano 6 专用 Disk Swap 修补。")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if session.source_kind == "official":
            self._build_official_ipsw(profile, output_path, new_mse, log)
        else:
            self._build_community_ipsw(Path(session.source_ipsw_path), output_path, new_mse, log)

        log(f"已生成 IPSW: {output_path}")
        return output_path

    def _build_official_ipsw(self, profile: DeviceProfile, output_path: Path, mse_bytes: bytes, log: LogFn) -> None:
        if not profile.template_dir:
            raise StudioError("当前设备没有可用的官方打包模板目录。")

        template_dir = self.project_root / profile.template_dir
        if not template_dir.exists():
            template_dir = self.resource_root / profile.template_dir
        if not template_dir.exists():
            raise StudioError(f"缺少官方模板目录: {template_dir}")

        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("Firmware.MSE", mse_bytes)
            for item in sorted(template_dir.rglob("*")):
                if item.is_dir():
                    continue
                arcname = item.relative_to(template_dir).as_posix()
                if arcname == "Firmware.MSE":
                    continue
                archive.write(item, arcname)
        log(f"已按官方模板重新封装 {profile.label} IPSW。")

    def _build_community_ipsw(self, source_ipsw: Path, output_path: Path, mse_bytes: bytes, log: LogFn) -> None:
        with zipfile.ZipFile(source_ipsw, "r") as source_archive, zipfile.ZipFile(
            output_path, "w", compression=zipfile.ZIP_STORED
        ) as target_archive:
            for info in source_archive.infolist():
                data = mse_bytes if info.filename == "Firmware.MSE" else source_archive.read(info.filename)
                target_archive.writestr(info, data)
        log("已在社区 IPSW 的基础上写回新的 Firmware.MSE。")

    def default_output_path(self) -> Path:
        session = self.load_session()
        if not session.device_key:
            return WORK_OUTPUTS / "custom_theme.ipsw"
        profile = self.get_profile(session.device_key)
        return WORK_OUTPUTS / profile.default_output_name

    def body_dir(self) -> Path:
        return WORK_BODY

    def saved_assets_dir(self) -> Path:
        SAVED_ROOT.mkdir(parents=True, exist_ok=True)
        return SAVED_ROOT


def _font_mode_label(mode: str) -> str:
    if mode == EXPERIMENTAL_FONT_MODE:
        return "实验性自动处理（仅 Heiti SC）"
    return "安全写入"


def _theme_studio_list_fonts(self: ThemeStudio) -> list[dict[str, object]]:
    inventory = _load_font_inventory()
    needs_rescan = False
    if inventory:
        if any("slot_id" not in item for item in inventory):
            needs_rescan = True
        else:
            inventory_names = {str(item.get("container_name", "")) for item in inventory} | {
                str(item.get("name", "")) for item in inventory
            }
            for container_name, children in SUPPORTED_TTC_CHILDREN.items():
                if container_name not in inventory_names:
                    continue
                child_slot_ids = {str(item.get("slot_id", "")) for item in inventory}
                if not all(child["slot_id"] in child_slot_ids for child in children):
                    needs_rescan = True
                    break
    if (not inventory or needs_rescan) and WORK_RSRC_BASE.exists():
        inventory = _scan_font_slots(WORK_RSRC_BASE)
        _save_font_inventory(inventory)

    replacements = _load_font_replacements()
    items: list[dict[str, object]] = []
    for item in inventory:
        slot_name = str(item.get("slot_id", item.get("name", "")))
        replacement = replacements.get(slot_name, {})
        replacement_path = str(replacement.get("source_path", "") or replacement.get("staged_path", ""))
        supported = bool(item.get("supported"))
        if replacement_path and supported:
            status = "已指定替换"
        elif supported:
            status = "原始"
        else:
            status = "暂不支持"
        items.append(
            {
                "name": slot_name,
                "display_name": str(item.get("display_name", slot_name)),
                "extension": str(item.get("extension", "")),
                "supported": supported,
                "status": status,
                "replacement_path": replacement_path,
                "kind": str(item.get("kind", "file")),
                "container_name": str(item.get("container_name", "")),
                "member_index": item.get("member_index"),
                "member_name": str(item.get("member_name", "")),
                "hint": str(item.get("hint", "")),
                "replacement_info": replacement,
                "mode_label": _font_mode_label(str(replacement.get("mode", SAFE_FONT_MODE))),
            }
        )
    return items


def _theme_studio_stage_font_replacement(
    self: ThemeStudio,
    slot_name: str,
    source_path: Path,
    mode: str = SAFE_FONT_MODE,
) -> Path:
    slot = next((item for item in self.list_fonts() if item["name"] == slot_name), None)
    if slot is None:
        raise StudioError(f"没有找到字体槽位: {slot_name}")
    if not bool(slot["supported"]):
        raise StudioError(f"{slot_name} 当前不是可替换的 .ttf 槽位")
    if mode not in {SAFE_FONT_MODE, EXPERIMENTAL_FONT_MODE}:
        raise StudioError(f"Unknown font replacement mode: {mode}")
    if mode == EXPERIMENTAL_FONT_MODE and slot_name != EXPERIMENTAL_HEITI_SC_SLOT:
        raise StudioError("实验性自动处理当前只支持 STHeiti-Medium.ttc / Heiti SC")
    if source_path.suffix.lower() != ".ttf":
        raise StudioError("当前只支持导入 .ttf 字体文件")
    if not source_path.exists():
        raise StudioError(f"没有找到要导入的字体文件: {source_path}")
    try:
        source_info = _analyze_font_file(source_path)
    except Exception as exc:
        raise StudioError("选中的文件不是可用的 TrueType 字体") from exc

    member_window_bytes: int | None = None
    if str(slot.get("kind")) == "ttc-member":
        container_bytes = _read_font_container_bytes(WORK_RSRC_BASE, str(slot.get("container_name") or ""))
        member_window_bytes = _ttc_member_window_bytes(container_bytes, int(slot.get("member_index", 0)))

    target_bytes = _read_font_slot_bytes(WORK_RSRC_BASE, slot)
    target_font = ttLib.TTFont(io.BytesIO(target_bytes))
    temp_subset_path: Path | None = None
    safe_name = slot_name.replace("::", "__").replace("/", "_")
    staged_path = WORK_FONTS / f"{safe_name}.{mode}.ttf"
    WORK_FONTS.mkdir(parents=True, exist_ok=True)
    replacements = _load_font_replacements()
    old_entry = replacements.get(slot_name, {})
    old_staged_path = Path(str(old_entry.get("staged_path", "")))
    try:
        target_info = _font_analysis(target_font, size_bytes=len(target_bytes))
        if mode == SAFE_FONT_MODE:
            _copy_file(source_path, staged_path)
        else:
            temp_subset_path = WORK_FONTS / f"{safe_name}.subset.ttf"
            _subset_font_to_text(source_path, temp_subset_path, _default_heiti_sc_subset_text())
            processed_font = ttLib.TTFont(str(temp_subset_path))
            try:
                _apply_target_font_identity(processed_font, target_font)
                output = io.BytesIO()
                processed_font.save(output)
                staged_path.write_bytes(output.getvalue())
            finally:
                processed_font.close()
    finally:
        target_font.close()
        if temp_subset_path and temp_subset_path.exists():
            temp_subset_path.unlink()

    if old_staged_path and old_staged_path.exists() and old_staged_path != staged_path:
        with contextlib.suppress(OSError):
            old_staged_path.unlink()

    staged_info = _analyze_font_file(staged_path)
    notes, warnings, risk_level = _build_font_import_report(
        slot,
        source_info,
        target_info,
        staged_info,
        mode=mode,
        member_window_bytes=member_window_bytes,
    )

    replacements[slot_name] = {
        "source_path": str(source_path.resolve()),
        "staged_path": str(staged_path.resolve()),
        "mode": mode,
        "processing_summary": "实验性自动处理后写入" if mode == EXPERIMENTAL_FONT_MODE else "安全写入（已处理字体）",
        "subset_profile": HEITI_SC_SUBSET_PROFILE if mode == EXPERIMENTAL_FONT_MODE else "",
        "source_size_bytes": int(source_info.get("size_bytes", 0)),
        "source_glyph_count": int(source_info.get("glyph_count", 0)),
        "source_vendor_id": str(source_info.get("vendor_id", "")),
        "staged_size_bytes": int(staged_info.get("size_bytes", 0)),
        "staged_glyph_count": int(staged_info.get("glyph_count", 0)),
        "staged_vendor_id": str(staged_info.get("vendor_id", "")),
        "target_size_bytes": int(target_info.get("size_bytes", 0)),
        "target_glyph_count": int(target_info.get("glyph_count", 0)),
        "target_vendor_id": str(target_info.get("vendor_id", "")),
        "risk_level": risk_level,
        "notes": notes,
        "warnings": warnings,
    }
    _save_font_replacements(replacements)
    return staged_path


def _theme_studio_apply_font_replacements(self: ThemeStudio, rsrc_path: Path, log: LogFn) -> list[str]:
    replacements = _load_font_replacements()
    if not replacements:
        return []

    font_items = {str(item["name"]): item for item in self.list_fonts()}
    fat = PyFatFS.PyFatFS(str(rsrc_path), read_only=False)
    applied: list[str] = []
    try:
        for slot_name, entry in replacements.items():
            slot = font_items.get(slot_name)
            if not slot or not bool(slot.get("supported")):
                log(f"字体槽位暂不支持替换，已跳过: {slot_name}")
                continue

            staged_path = Path(str(entry.get("staged_path", "")))
            if not staged_path.exists():
                log(f"没有找到已暂存的替换字体，已跳过: {slot_name}")
                continue

            slot_kind = str(slot.get("kind", "file"))
            container_name = str(slot.get("container_name") or slot_name)
            fs_path = f"/Resources/Fonts/{container_name}"
            mode = str(entry.get("mode", SAFE_FONT_MODE))

            if slot_kind == "ttc-member":
                collection = None
                replacement_font = None
                try:
                    with fat.openbin(fs_path, mode="rb") as stream:
                        container_bytes = stream.read()
                    collection = ttLib.TTCollection(io.BytesIO(container_bytes))
                    member_index = int(slot.get("member_index", 0))
                    original_font = collection.fonts[member_index]
                    replacement_font = ttLib.TTFont(str(staged_path))
                    _apply_target_font_identity(replacement_font, original_font)
                    rebuilt_container = _replace_ttc_member_preserving_shell(container_bytes, member_index, replacement_font)

                    fat.remove(fs_path)
                    with fat.openbin(fs_path, mode="wb") as stream:
                        stream.write(rebuilt_container)
                    applied.append(slot_name)
                    if mode == EXPERIMENTAL_FONT_MODE:
                        log(f"已按实验模式预处理后写入字体槽位: {slot_name}")
                    else:
                        log(f"已直接写入已处理字体: {slot_name}")
                except Exception as exc:
                    raise StudioError(f"写回字体槽位失败: {slot_name} ({exc})") from exc
                finally:
                    if replacement_font is not None:
                        replacement_font.close()
                    if collection is not None:
                        collection.close()
                continue

            original_font = None
            replacement_font = None
            try:
                with fat.openbin(fs_path, mode="rb") as stream:
                    original_font = ttLib.TTFont(io.BytesIO(stream.read()))
                replacement_font = ttLib.TTFont(str(staged_path))
                _apply_target_font_identity(replacement_font, original_font)

                output = io.BytesIO()
                replacement_font.save(output)

                fat.remove(fs_path)
                with fat.openbin(fs_path, mode="wb") as stream:
                    stream.write(output.getvalue())
                applied.append(slot_name)
                if mode == EXPERIMENTAL_FONT_MODE:
                    log(f"已按实验模式预处理后写入字体槽位: {slot_name}")
                else:
                    log(f"已直接写入已处理字体: {slot_name}")
            except Exception as exc:
                raise StudioError(f"写回字体槽位失败: {slot_name} ({exc})") from exc
            finally:
                if replacement_font is not None:
                    replacement_font.close()
                if original_font is not None:
                    original_font.close()
    finally:
        fat.close()

    return applied


ThemeStudio.list_fonts = _theme_studio_list_fonts
ThemeStudio.stage_font_replacement = _theme_studio_stage_font_replacement
ThemeStudio.apply_font_replacements = _theme_studio_apply_font_replacements
