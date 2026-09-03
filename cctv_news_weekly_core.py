"""Reusable CCTV News Weekly discovery and HLS download core."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

INDEX_URL = "https://tv.cctv.com/lm/xwzk/tongyong/index.shtml"
COLUMN_ID = "TOPC1451559180488841"
VIDEO_LIST_URL = "https://api.cntv.cn/NewVideo/getVideoListByColumn?id={column_id}&n=20&sort=desc&p=1&d=&mode=0&serviceId=tvcctv&callback=cctvMvp"
VIDEO_INFO_URL = "https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do?pid={guid}&tai=ipad&client=html5&im=1"
USER_AGENT = "cctv-news-weekly-desktop/0.2 (+personal-use)"
QUALITY_NAMES = ("流畅", "标清", "高清", "超清")
QUALITY_CODES = {"450", "850", "1200", "2000"}
GUID_RE = re.compile(r'\bvar\s+guid\s*=\s*["\']([0-9a-f]{32})["\']', re.I)
EPISODE_RE = re.compile(r"https?://tv\.cctv\.com/(\d{4})/(\d{2})/(\d{2})/VIDE[A-Za-z0-9]+\.shtml")


class CctvError(RuntimeError):
    """A user-facing failure."""


class DownloadCanceled(CctvError):
    """Raised when the user cancels an active FFmpeg job."""


@dataclass(frozen=True)
class Episode:
    title: str
    url: str
    date: datetime
    guid: str | None = None
    duration: str | None = None


@dataclass(frozen=True)
class StreamVariant:
    quality: str
    bandwidth: int
    resolution: str
    url: str


@dataclass(frozen=True)
class ResolvedEpisode:
    episode: Episode
    guid: str
    info: dict
    variants: tuple[StreamVariant, ...]
    duration_seconds: float


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._text).strip()))
            self._href = None
            self._text = []


def fetch_bytes(url: str, timeout: float = 30, attempts: int = 2) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last_error = exc
            if attempt + 1 < max(1, attempts):
                time.sleep(0.4 * (attempt + 1))
    raise CctvError(f"请求失败（已重试 {max(1, attempts)} 次）: {url}\n{last_error}") from last_error


def fetch_text(url: str, timeout: float = 30) -> str:
    raw = fetch_bytes(url, timeout)
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def fetch_jsonp(url: str, timeout: float = 30) -> dict:
    response = fetch_text(url, timeout).strip()
    start, end = response.find("("), response.rfind(")")
    if start < 0 or end <= start:
        raise CctvError("央视列表接口返回的不是有效 JSONP。")
    try:
        return json.loads(response[start + 1 : end])
    except json.JSONDecodeError as exc:
        raise CctvError("央视列表接口返回的 JSONP 内容无法解析。") from exc


def parse_episode_links(index_html: str) -> list[Episode]:
    parser = LinkParser()
    parser.feed(index_html)
    result: dict[str, Episode] = {}
    for href, text in parser.links:
        href, text = html.unescape(href), html.unescape(text)
        match = EPISODE_RE.search(href)
        if not match or "新闻周刊" not in text:
            continue
        result[href] = Episode(text, href, datetime(*map(int, match.groups())))
    return sorted(result.values(), key=lambda item: item.date, reverse=True)


def list_episodes(limit: int = 20, timeout: float = 30) -> list[Episode]:
    if limit < 1:
        return []
    try:
        payload = fetch_jsonp(VIDEO_LIST_URL.format(column_id=COLUMN_ID), timeout)
        rows = (payload.get("data") or {}).get("list") or []
        episodes: list[Episode] = []
        for row in rows:
            title, url = str(row.get("title") or ""), str(row.get("url") or "")
            if "新闻周刊" not in title or not url:
                continue
            try:
                date = datetime.strptime(str(row.get("time") or "")[:10], "%Y-%m-%d")
            except ValueError:
                continue
            episodes.append(Episode(title, url, date, str(row.get("guid") or "") or None, str(row.get("length") or "") or None))
        if episodes:
            return sorted(episodes, key=lambda item: item.date, reverse=True)[:limit]
    except CctvError:
        pass
    fallback = parse_episode_links(fetch_text(INDEX_URL, timeout))[:limit]
    if not fallback:
        raise CctvError("央视列表接口和栏目页都没有找到《新闻周刊》节目。")
    return fallback


def extract_guid(detail_html: str) -> str:
    match = GUID_RE.search(detail_html)
    if not match:
        raise CctvError("详情页没有找到播放器 GUID，页面结构可能已变化。")
    return match.group(1)


def fetch_video_info(guid: str, timeout: float = 30) -> dict:
    try:
        data = json.loads(fetch_text(VIDEO_INFO_URL.format(guid=guid), timeout))
    except json.JSONDecodeError as exc:
        raise CctvError("央视视频接口返回的不是有效 JSON。") from exc
    if data.get("ack") != "yes" or data.get("status") not in ("001", 1):
        raise CctvError(f"央视视频接口拒绝请求: {data.get('tip_msg') or data}")
    if str(data.get("is_protected", "0")) == "1":
        raise CctvError("该视频标记为受保护内容，程序不会尝试绕过保护。")
    return data


def parse_duration(value: object) -> float:
    text = str(value or "")
    if ":" in text:
        try:
            values = [float(part) for part in text.split(":")]
            return values[-1] + 60 * values[-2] + (3600 * values[-3] if len(values) > 2 else 0)
        except ValueError:
            return 0
    try:
        return float(text)
    except ValueError:
        return 0


def get_master_url(info: dict) -> str:
    manifest = info.get("manifest") or {}
    url = manifest.get("hls_h5e_url") or info.get("hls_url")
    if not isinstance(url, str) or not url:
        raise CctvError("视频接口没有提供可用的 HLS 播放列表地址。")
    return url


def parse_attrs(value: str) -> dict[str, str]:
    return {key: raw.strip().strip('"') for key, raw in re.findall(r'([A-Z0-9-]+)=((?:"[^"]*")|[^,]*)', value)}


def parse_master_playlist(master_url: str, playlist: str) -> list[StreamVariant]:
    if "#EXT-X-KEY:" in playlist:
        raise CctvError("HLS 播放列表包含加密密钥，程序不会尝试解密。")
    raw_variants: list[tuple[int, str, str]] = []
    pending: dict[str, str] | None = None
    for line in playlist.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending = parse_attrs(line.split(":", 1)[1])
        elif pending is not None and line and not line.startswith("#"):
            raw_variants.append((int(pending.get("BANDWIDTH", "0")), pending.get("RESOLUTION", "未知"), urljoin(master_url, line)))
            pending = None
    if not raw_variants:
        return [StreamVariant("高清", 0, "未知", master_url)]
    raw_variants.sort(key=lambda item: item[0])
    labels = QUALITY_NAMES if len(raw_variants) == len(QUALITY_NAMES) else tuple(f"档位{i + 1}" for i in range(len(raw_variants)))
    return [StreamVariant(label, bandwidth, resolution, url) for label, (bandwidth, resolution, url) in zip(labels, raw_variants)]


def rewrite_to_clean_hls_cdn(variants: list[StreamVariant], hls_url: str | None) -> list[StreamVariant]:
    """Use the API's alternate HLS CDN for downloads when it is available.

    The H5 CDN can occasionally return corrupted TS packets while the regular
    public HLS CDN serves the same rendition cleanly. Both paths use the same
    GUID and rendition directory, so only the host and `/h5e/` path segment
    need to be changed.
    """
    if not hls_url:
        return variants
    source = urlsplit(hls_url)
    if not source.scheme or not source.netloc:
        return variants
    rewritten: list[StreamVariant] = []
    for variant in variants:
        target = urlsplit(variant.url)
        path = target.path.replace("/asp/h5e/hls/", "/asp/hls/", 1)
        if path == target.path:
            path = target.path.replace("/asp/hls/", "/asp/hls/", 1)
        rewritten.append(StreamVariant(variant.quality, variant.bandwidth, variant.resolution, urlunsplit((source.scheme, source.netloc, path, "", ""))))
    return rewritten


def bundled_ffprobe_path() -> str:
    """Return the packaged ffprobe path, or the system command."""
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidate = bundle_root / "bin" / "ffprobe.exe"
    return str(candidate) if candidate.is_file() else "ffprobe"


def probe_variant_resolution(variant: StreamVariant, ffprobe_path: str, timeout: float = 10) -> tuple[int, int] | None:
    if shutil.which(ffprobe_path) is None and not Path(ffprobe_path).exists():
        return None
    command = [
        ffprobe_path, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0", variant.url,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=max(3, timeout), check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"(\d+)\s*,\s*(\d+)", result.stdout)
    return (int(match.group(1)), int(match.group(2))) if match else None


def align_variants(variants: list[StreamVariant], ffprobe_path: str | None = None, timeout: float = 10) -> list[StreamVariant]:
    """Replace nominal playlist resolutions with actual downloadable sizes."""
    if not ffprobe_path:
        return variants
    probed: list[tuple[StreamVariant, tuple[int, int]]] = []
    for variant in variants:
        resolution = probe_variant_resolution(variant, ffprobe_path, timeout)
        if resolution:
            probed.append((variant, resolution))
    if not probed:
        return variants

    # Keep one efficient rendition for duplicate effective resolutions. A true
    # 720p source may legitimately have separate HD and UHD bitrates, so retain
    # both only when the measured resolution is actually 1280x720 or larger.
    groups: dict[tuple[int, int], list[tuple[StreamVariant, tuple[int, int]]]] = {}
    for item in probed:
        groups.setdefault(item[1], []).append(item)
    aligned: list[StreamVariant] = []
    for resolution, items in sorted(groups.items(), key=lambda pair: (pair[0][0] * pair[0][1], pair[1][0][0].bandwidth)):
        items.sort(key=lambda item: item[0].bandwidth)
        keep = items if resolution[0] >= 1280 and len(items) > 1 else [items[0]]
        labels = ("高清", "超清") if len(keep) == 2 and resolution[0] >= 1280 else (("高清",) if resolution[0] >= 1280 else (("标清",) if resolution[0] >= 640 else ("流畅",)))
        for index, (variant, _) in enumerate(keep):
            label = labels[min(index, len(labels) - 1)]
            aligned.append(StreamVariant(label, variant.bandwidth, f"{resolution[0]}x{resolution[1]}", variant.url))
    return aligned


def resolve_episode(episode: Episode, timeout: float = 30, ffprobe_path: str | None = None) -> ResolvedEpisode:
    guid = episode.guid or extract_guid(fetch_text(episode.url, timeout))
    info = fetch_video_info(guid, timeout)
    manifest = info.get("manifest") or {}
    master_urls = [manifest.get("hls_h5e_url"), info.get("hls_url")]
    last_error: CctvError | None = None
    variants: list[StreamVariant] = []
    for master_url in dict.fromkeys(url for url in master_urls if isinstance(url, str) and url):
        try:
            variants = parse_master_playlist(master_url, fetch_text(master_url, timeout))
            variants = rewrite_to_clean_hls_cdn(variants, info.get("hls_url"))
            break
        except CctvError as exc:
            last_error = exc
    if not variants:
        raise last_error or CctvError("没有可用的 HLS 播放列表。")
    variants = align_variants(variants, ffprobe_path, min(timeout, 10))
    duration = parse_duration((info.get("video") or {}).get("totalLength") or info.get("len") or episode.duration)
    return ResolvedEpisode(episode, guid, info, tuple(variants), duration)


def variant_quality_code(variant: StreamVariant) -> str:
    path_parts = urlparse(variant.url).path.split("/")
    for part in path_parts:
        if part in QUALITY_CODES:
            return part
        match = re.fullmatch(r"(450|850|1200|2000)\.m3u8", part)
        if match:
            return match.group(1)
    return ""


def choose_variant(variants: tuple[StreamVariant, ...] | list[StreamVariant], requested: str | None = None) -> StreamVariant:
    if not variants:
        raise CctvError("没有可用的清晰度。")
    if not requested:
        return variants[-1]
    for variant in variants:
        if requested in {variant.quality, str(variant.bandwidth), variant_quality_code(variant)}:
            return variant
    raise CctvError(f"找不到清晰度 {requested}。")


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned or "cctv_news_weekly"


def next_available_path(directory: Path, stem: str, suffix: str = ".mp4") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate, index = directory / f"{stem}{suffix}", 1
    while candidate.exists():
        candidate = directory / f"{stem} ({index}){suffix}"
        index += 1
    return candidate


def desktop_path() -> Path:
    return Path.home() / "Desktop"


def bundled_ffmpeg_path() -> str:
    """Return the packaged FFmpeg path when running from a PyInstaller build."""
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidate = bundle_root / "bin" / "ffmpeg.exe"
    return str(candidate) if candidate.is_file() else "ffmpeg"


ProgressCallback = Callable[[int], None]


def download_variant(
    variant: StreamVariant,
    output: Path,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    duration_seconds: float = 0,
    ffmpeg_path: str = "ffmpeg",
    timeout: float = 30,
    max_seconds: float | None = None,
) -> Path:
    if shutil.which(ffmpeg_path) is None and not Path(ffmpeg_path).exists():
        raise CctvError(f"找不到 FFmpeg: {ffmpeg_path}")
    if "#EXT-X-KEY:" in fetch_text(variant.url, timeout):
        raise CctvError("HLS 播放列表包含加密密钥，程序不会尝试解密。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=output.stem + ".", suffix=".part.mp4", dir=output.parent, delete=False) as temp:
        temp_path = Path(temp.name)
    command = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y", "-i", variant.url]
    if max_seconds is not None:
        command.extend(["-t", str(max_seconds)])
    command.extend(["-c", "copy", str(temp_path)])
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if cancel_event and cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise DownloadCanceled("下载已取消。")
            key, _, value = line.strip().partition("=")
            if key == "out_time_ms" and duration_seconds and progress_callback:
                progress_callback(max(0, min(99, int(float(value) / 1_000_000 / duration_seconds * 100))))
            elif key == "progress" and value == "end" and progress_callback:
                progress_callback(100)
        stderr = process.stderr.read() if process.stderr else ""
        if process.wait() != 0:
            raise CctvError(f"FFmpeg 下载失败: {stderr.strip() or '未知错误'}")
        temp_path.replace(output)
        if progress_callback:
            progress_callback(100)
        return output
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        temp_path.unlink(missing_ok=True)
