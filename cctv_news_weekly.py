#!/usr/bin/env python3
"""Command-line entry point for the CCTV News Weekly downloader."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from cctv_news_weekly_core import (
    CctvError, Episode, bundled_ffprobe_path, choose_variant, download_variant, list_episodes,
    next_available_path, resolve_episode, safe_filename,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载央视《新闻周刊》公开 HLS 视频")
    parser.add_argument("--episode-url", help="指定详情页 URL；默认使用最新节目")
    parser.add_argument("--list", action="store_true", help="列出最新节目和清晰度，不下载")
    parser.add_argument("--quality", help="清晰度名称或码率，例如 高清、超清、2000")
    parser.add_argument("--output-dir", default="downloads", help="输出目录")
    parser.add_argument("--output", help="完整输出文件路径")
    parser.add_argument("--max-seconds", type=float, help="仅下载前 N 秒，用于测试")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default=None)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.episode_url:
            episode = Episode("指定节目", args.episode_url, datetime.min)
        else:
            episodes = list_episodes(20, args.timeout)
            if not episodes:
                raise CctvError("没有找到《新闻周刊》节目。")
            episode = episodes[0]
        resolved = resolve_episode(episode, args.timeout, args.ffprobe or bundled_ffprobe_path())
        payload = {
            "title": resolved.info.get("title") or episode.title,
            "date": episode.date.strftime("%Y-%m-%d") if episode.date.year > 1 else None,
            "url": episode.url,
            "guid": resolved.guid,
            "duration": resolved.duration_seconds,
            "variants": [variant.__dict__ for variant in resolved.variants],
        }
        if args.list:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        variant = choose_variant(resolved.variants, args.quality)
        title = safe_filename(str(payload["title"]))
        output = Path(args.output) if args.output else next_available_path(Path(args.output_dir), title)
        download_variant(variant, output, duration_seconds=resolved.duration_seconds, ffmpeg_path=args.ffmpeg, timeout=args.timeout, max_seconds=args.max_seconds)
        print(f"下载完成: {output}")
        return 0
    except CctvError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
