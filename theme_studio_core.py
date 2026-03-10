from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
import contextlib
import io
import json
import shutil
import sys
import urllib.request
import zipfile

from PIL import Image
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
SESSION_PATH = STUDIO_ROOT / "session.json"

WORK_BODY = CURRENT_ROOT / "body"
WORK_EXPORTS = CURRENT_ROOT / "exports"
WORK_INPUTS = CURRENT_ROOT / "inputs"
WORK_OUTPUTS = CURRENT_ROOT / "outputs"
WORK_TMP = CURRENT_ROOT / "tmp"
WORK_INVENTORY = CURRENT_ROOT / "artwork_index.json"
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
    {"key": "n7-icons", "label": "Nano 7 图标", "devices": {"nano7-2012", "nano7-2015"}},
    {"key": "n7-wallpapers", "label": "Nano 7 壁纸（全部）", "devices": {"nano7-2012", "nano7-2015"}},
    {"key": "n7-wallpapers-full", "label": "Nano 7 壁纸（240x432）", "devices": {"nano7-2012", "nano7-2015"}},
    {"key": "n7-wallpapers-thumb", "label": "Nano 7 壁纸缩略图（117x200）", "devices": {"nano7-2012", "nano7-2015"}},
]


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
    for path in [STUDIO_ROOT, CURRENT_ROOT, WORK_BODY, WORK_EXPORTS, WORK_INPUTS, WORK_OUTPUTS, WORK_TMP]:
        path.mkdir(parents=True, exist_ok=True)


def _reset_current_workspace() -> None:
    if CURRENT_ROOT.exists():
        shutil.rmtree(CURRENT_ROOT)
    _mkdirs()


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _patch_nano7_mse(mse_bytes: bytes) -> bytes:
    pattern = b"87402.0\x04"
    index = mse_bytes.find(pattern)
    if index == -1:
        raise StudioError("没有在 nano 7 的 Firmware.MSE 里找到预期的补丁签名。")
    patched = bytearray(mse_bytes)
    patched[index + len(pattern) - 1] = 0x03
    return bytes(patched)


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
            log(f"官方固件备份已存在: {backup_path.name}")
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

        source_copy = WORK_INPUTS / ipsw_path.name
        _copy_file(ipsw_path, source_copy)
        log(f"已复制社区 IPSW 到工作区: {source_copy.name}")

        with zipfile.ZipFile(source_copy, "r") as archive:
            try:
                mse_bytes = archive.read("Firmware.MSE")
            except KeyError as exc:
                raise StudioError("所选 IPSW 中没有找到 Firmware.MSE。") from exc

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
        if not inventory:
            if packed_size is None:
                return "容量提醒：重新导入一次官方固件或社区 IPSW 后，才能更准确地追踪哪些素材被升级成了 1888。"

            original_size = WORK_SILVER.stat().st_size if WORK_SILVER.exists() else 0
            delta = packed_size - original_size
            return (
                f"容量提醒：原始 SilverImagesDB 为 {_format_bytes(original_size)}，"
                f"当前为 {_format_bytes(packed_size)}，变化 {delta / (1024 * 1024):+.2f} MB。"
                "由于当前工作区缺少初始素材清单，暂时无法统计 1888 升格数量。"
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
        if delta <= 0:
            risk = "当前打包后的 SilverImagesDB 没有比原始包更大。"
        elif delta < 512 * 1024:
            risk = "当前增量较小，但仍建议实际刷机前保留原始备份。"
        elif delta < 2 * 1024 * 1024:
            risk = "当前增量已经比较明显，若还要继续把更多素材转成 1888，需要留意超限风险。"
        else:
            risk = "当前增量较大，已经属于高风险体积增长，建议减少 1888 大图替换。"

        promoted_suffix = f" 已升格到 1888 的素材数：{len(promoted)}。" if promoted else ""
        return (
            f"容量提醒：原始 SilverImagesDB 为 {_format_bytes(original_size)}，"
            f"当前为 {_format_bytes(packed_size)}，变化 {delta / (1024 * 1024):+.2f} MB。"
            f"{risk}{promoted_suffix}"
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

    def repack_silverdb(self, log: LogFn) -> Path:
        if not WORK_BODY.exists():
            raise StudioError("还没有可打包的 body 目录。")

        with contextlib.redirect_stdout(io.StringIO()):
            with WORK_SILVER_PACKED.open("wb") as stream:
                pack_silverdb(stream, WORK_BODY)
        log(f"已生成 {WORK_SILVER_PACKED.name}")
        return WORK_SILVER_PACKED

    def build_ipsw(self, output_path: Path, log: LogFn) -> Path:
        session = self.load_session()
        if not session.device_key or not session.source_kind:
            raise StudioError("请先导入官方固件或社区 IPSW。")

        profile = self.get_profile(session.device_key)
        silver_path = self.repack_silverdb(log)
        log(self.capacity_summary(silver_path.stat().st_size))
        _copy_file(WORK_RSRC_BASE, WORK_RSRC_PATCHED)
        _replace_silverdb(WORK_RSRC_PATCHED, silver_path)
        log("已把新的 SilverImagesDB 写回 rsrc 分区。")

        mse = _parse_mse(WORK_MSE_BASE.read_bytes(), profile.family)
        section = next((item for item in mse.sections if item.name == b"crsr"), None)
        if section is None:
            raise StudioError("回写时没有找到 rsrc/crsr 分区。")

        img1 = _parse_img1(section.body, profile.family)
        img1.body = WORK_RSRC_PATCHED.read_bytes()
        section.body = _build_img1(img1)
        new_mse = _build_mse(mse)

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
            for item in sorted(template_dir.rglob("*")):
                if item.is_dir():
                    continue
                arcname = item.relative_to(template_dir).as_posix()
                if arcname == "Firmware.MSE":
                    archive.writestr(arcname, mse_bytes)
                else:
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
