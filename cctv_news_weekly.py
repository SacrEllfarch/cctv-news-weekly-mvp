#!/usr/bin/env python3
"""Download public CCTV News Weekly HLS videos.

The script deliberately uses the public page/API and FFmpeg. It does not
attempt to bypass DRM or protected streams.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen


INDEX_URL = "https://tv.cctv.com/lm/xwzk/tongyong/index.shtml"
COLUMN_ID = "TOPC1451559180488841"
VIDEO_LIST_URL = (
    "https://api.cntv.cn/NewVideo/getVideoListByColumn"
    "?id={column_id}&n=20&sort=desc&p=1&d=&mode=0&serviceId=tvcctv&callback=cctvMvp"
)
VIDEO_INFO_URL = (
    "https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do"
    "?pid={guid}&tai=ipad&client=html5&im=1"
)
USER_AGENT = "cctv-news-weekly-mvp/0.1 (+personal-use)"
QUALITY_NAMES = ("流畅", "标清", "高清", "超清")
GUID_RE = re.compile(r'\bvar\s+guid\s*=\s*["\']([0-9a-f]{32})["\']', re.I)
EPISODE_RE = re.compile(
    r"https?://tv\.cctv\.com/(\d{4})/(\d{2})/(\d{2})/"
    r"(VIDE[A-Za-z0-9]+)\.shtml"
)


class CctvError(RuntimeError):
    """A user-facing failure with a concise explanation."""


@dataclass(frozen=True)
class Episode:
    title: str
    url: str
    date: datetime
    guid: str | None = None


@dataclass(frozen=True)
class StreamVariant:
    quality: str
    bandwidth: int
    resolution: str
    url: str


class LinkParser(HTMLParser):
    """Collect anchor text and href values without requiring BeautifulSoup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = dict(attrs)
        self._href = attr_map.get("href")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._text).strip()))
            self._href = None
            self._text = []


def fetch_bytes(url: str, timeout: float) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:  # urllib exposes several platform-specific errors.
        raise CctvError(f"请求失败: {url}\n{exc}") from exc


def fetch_text(url: str, timeout: float) -> str:
    raw = fetch_bytes(url, timeout)
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_jsonp(url: str, timeout: float) -> dict:
    response = fetch_text(url, timeout).strip()
    start = response.find("(")
    end = response.rfind(")")
    if start < 0 or end <= start:
        raise CctvError("央视列表接口返回的不是有效 JSONP。")
    try:
        return json.loads(response[start + 1 : end])
    except json.JSONDecodeError as exc:
        raise CctvError("央视列表接口返回的 JSONP 内容无法解析。") from exc


def parse_episode_links(index_html: str) -> list[Episode]:
    parser = LinkParser()
    parser.feed(index_html)
    episodes: dict[str, Episode] = {}
    for href, text in parser.links:
        match = EPISODE_RE.search(html.unescape(href))
        if not match or "新闻周刊" not in html.unescape(text):
            continue
        year, month, day, _ = match.groups()
        date = datetime(int(year), int(month), int(day))
        normalized_url = href.replace("&amp;", "&")
        episodes[normalized_url] = Episode(text, normalized_url, date)
    return sorted(episodes.values(), key=lambda item: item.date, reverse=True)


def find_latest_episode(timeout: float) -> Episode:
    # The page loads its list asynchronously; use the same public JSONP API
    # instead of relying on browser-rendered HTML.
    try:
        payload = fetch_jsonp(VIDEO_LIST_URL.format(column_id=COLUMN_ID), timeout)
        rows = (payload.get("data") or {}).get("list") or []
        episodes: list[Episode] = []
        for row in rows:
            title = str(row.get("title") or "")
            url = str(row.get("url") or "")
            if "新闻周刊" not in title or not url:
                continue
            date_text = str(row.get("time") or "")[:10]
            try:
                date = datetime.strptime(date_text, "%Y-%m-%d")
            except ValueError:
                continue
            episodes.append(Episode(title, url, date, str(row.get("guid") or "") or None))
        if episodes:
            return max(episodes, key=lambda item: item.date)
    except CctvError:
        pass

    # Keep a small fallback for a temporary API failure or a future API change.
    episodes = parse_episode_links(fetch_text(INDEX_URL, timeout))
    if not episodes:
        raise CctvError("央视列表接口和栏目页都没有找到《新闻周刊》节目。")
    return episodes[0]


def extract_guid(detail_html: str) -> str:
    match = GUID_RE.search(detail_html)
    if not match:
        raise CctvError("详情页没有找到播放器 GUID，页面结构可能已变化。")
    return match.group(1)


def fetch_video_info(guid: str, timeout: float) -> dict:
    url = VIDEO_INFO_URL.format(guid=guid)
    try:
        data = json.loads(fetch_text(url, timeout))
    except json.JSONDecodeError as exc:
        raise CctvError("央视视频接口返回的不是有效 JSON。") from exc
    if data.get("ack") != "yes" or data.get("status") not in ("001", 1):
        raise CctvError(f"央视视频接口拒绝请求: {data.get('tip_msg') or data}")
    if str(data.get("is_protected", "0")) == "1":
        raise CctvError("该视频标记为受保护内容，MVP 不会尝试绕过保护。")
    return data


def get_master_url(info: dict) -> str:
    manifest = info.get("manifest") or {}
    # hls_h5e_url is the public H5 stream used by the page's player.
    url = manifest.get("hls_h5e_url") or info.get("hls_url")
    if not isinstance(url, str) or not url:
        raise CctvError("视频接口没有提供可用的 HLS 播放列表地址。")
    return url


def parse_attrs(value: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for key, raw in re.findall(r'([A-Z0-9-]+)=((?:"[^"]*")|[^,]*)', value):
        attrs[key] = raw.strip().strip('"')
    return attrs


def parse_master_playlist(master_url: str, playlist: str) -> list[StreamVariant]:
    if "#EXT-X-KEY:" in playlist:
        raise CctvError("HLS 播放列表包含加密密钥，MVP 不会尝试解密。")

    variants: list[tuple[int, str, str]] = []
    pending: dict[str, str] | None = None
    for line in playlist.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending = parse_attrs(line.split(":", 1)[1])
        elif pending is not None and line and not line.startswith("#"):
            bandwidth = int(pending.get("BANDWIDTH", "0"))
            resolution = pending.get("RESOLUTION", "未知")
            variants.append((bandwidth, resolution, urljoin(master_url, line)))
            pending = None

    if not variants:
        # A media playlist is still a valid input for FFmpeg.
        return [StreamVariant("高清", 0, "未知", master_url)]

    variants.sort(key=lambda item: item[0])
    if len(variants) == len(QUALITY_NAMES):
        labels = QUALITY_NAMES
    else:
        labels = tuple(f"档位{i + 1}" for i in range(len(variants)))
    return [
        StreamVariant(label, bandwidth, resolution, url)
        for label, (bandwidth, resolution, url) in zip(labels, variants)
    ]


def resolve_episode(url: str, timeout: float, guid: str | None = None) -> tuple[Episode | None, str, dict, list[StreamVariant]]:
    if not guid:
        detail_html = fetch_text(url, timeout)
        guid = extract_guid(detail_html)
    info = fetch_video_info(guid, timeout)
    master_url = get_master_url(info)
    playlist = fetch_text(master_url, timeout)
    variants = parse_master_playlist(master_url, playlist)
    return None, guid, info, variants


def choose_variant(variants: list[StreamVariant], requested: str | None) -> StreamVariant:
    if not requested:
        return variants[-1]
    for variant in variants:
        quality_code = next(
            (part for part in urlparse(variant.url).path.split("/") if part in {"450", "850", "1200", "2000"}),
            "",
        )
        if requested in (variant.quality, str(variant.bandwidth), quality_code):
            return variant
    available = ", ".join(f"{v.quality}({v.bandwidth})" for v in variants)
    raise CctvError(f"找不到清晰度 {requested}，可选: {available}")


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value or "cctv_news_weekly"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载央视《新闻周刊》公开 HLS 视频")
    parser.add_argument("--episode-url", help="指定详情页 URL；默认自动选择栏目页最新节目")
    parser.add_argument("--list", action="store_true", help="列出节目元数据和清晰度，不下载")
    parser.add_argument("--quality", help="清晰度名称或码率，例如 高清、超清、2000")
    parser.add_argument("--output-dir", default="downloads", help="输出目录，默认 downloads")
    parser.add_argument("--output", help="完整输出文件路径，优先于 --output-dir")
    parser.add_argument("--max-seconds", type=float, help="仅下载前 N 秒，用于冒烟测试")
    parser.add_argument("--timeout", type=float, default=30, help="单次网络请求超时秒数")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg 可执行文件路径")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出元数据")
    return parser.parse_args()


def print_metadata(episode: Episode, guid: str, info: dict, variants: list[StreamVariant], as_json: bool) -> None:
    if as_json:
        payload = {
            "title": info.get("title") or episode.title,
            "date": episode.date.strftime("%Y-%m-%d"),
            "url": episode.url,
            "guid": guid,
            "duration": info.get("video", {}).get("totalLength") or info.get("len"),
            "variants": [variant.__dict__ for variant in variants],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"节目: {info.get('title') or episode.title}")
    print(f"日期: {episode.date:%Y-%m-%d}    时长: {info.get('video', {}).get('totalLength') or info.get('len', '未知')}")
    print(f"GUID: {guid}")
    print("清晰度:")
    for index, variant in enumerate(variants, 1):
        bitrate = f"{variant.bandwidth / 1000:.1f} Kbps" if variant.bandwidth else "媒体播放列表"
        print(f"  {index}. {variant.quality:<3} {variant.resolution:<9} {bitrate:<14} {variant.url}")


def download_variant(variant: StreamVariant, output: Path, ffmpeg: str, max_seconds: float | None) -> None:
    if shutil.which(ffmpeg) is None and not Path(ffmpeg).exists():
        raise CctvError(f"找不到 FFmpeg: {ffmpeg}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=output.stem + ".", suffix=".part.mp4", dir=output.parent, delete=False) as temp:
        temp_path = Path(temp.name)
    command = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-y", "-i", variant.url]
    if max_seconds is not None:
        command.extend(["-t", str(max_seconds)])
    command.extend(["-c", "copy", str(temp_path)])
    print(f"开始下载: {variant.quality} -> {output}")
    try:
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            raise CctvError(f"FFmpeg 退出码 {completed.returncode}，临时文件保留在: {temp_path}")
        temp_path.replace(output)
    except FileNotFoundError as exc:
        raise CctvError(f"无法启动 FFmpeg: {ffmpeg}") from exc
    finally:
        if temp_path.exists() and temp_path != output:
            temp_path.unlink(missing_ok=True)
    print(f"下载完成: {output} ({output.stat().st_size / 1024 / 1024:.1f} MB)")


def main() -> int:
    args = parse_args()
    try:
        episode = (
            Episode("指定节目", args.episode_url, datetime.min, None)
            if args.episode_url
            else find_latest_episode(args.timeout)
        )
        _, guid, info, variants = resolve_episode(episode.url, args.timeout, episode.guid)
        if episode.date == datetime.min:
            title = str(info.get("title") or "")
            date_match = re.search(r"(20\d{2})(\d{2})(\d{2})", title)
            if date_match:
                episode = Episode(title, episode.url, datetime.strptime(date_match.group(1) + date_match.group(2) + date_match.group(3), "%Y%m%d"))
        print_metadata(episode, guid, info, variants, args.json or args.list)
        if args.list:
            return 0

        requested = args.quality
        if not requested and sys.stdin.isatty():
            requested = input("选择清晰度（直接回车使用最高）：").strip() or None
        variant = choose_variant(variants, requested)
        title = safe_filename(str(info.get("title") or f"新闻周刊_{episode.date:%Y%m%d}"))
        output = Path(args.output) if args.output else Path(args.output_dir) / f"{title}.mp4"
        download_variant(variant, output, args.ffmpeg, args.max_seconds)
        return 0
    except CctvError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
