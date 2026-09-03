from pathlib import Path

import pytest

from cctv_news_weekly_core import CctvError, choose_variant, desktop_path, next_available_path, parse_duration, parse_master_playlist, rewrite_to_clean_hls_cdn, safe_filename


def test_parse_master_playlist_and_quality_labels():
    playlist = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=460800,RESOLUTION=480x270
450.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=870400,RESOLUTION=640x360
850.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1228800,RESOLUTION=1280x720
1200.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2048000,RESOLUTION=1280x720
2000.m3u8
"""
    variants = parse_master_playlist("https://cdn.example/a/main.m3u8", playlist)
    assert [variant.quality for variant in variants] == ["流畅", "标清", "高清", "超清"]
    assert choose_variant(variants, "450").bandwidth == 460800
    assert choose_variant(variants).bandwidth == 2048000
    realistic = parse_master_playlist(
        "https://dh5.example/asp/h5e/hls/main/x/main.m3u8",
        playlist,
    )
    cleaned = rewrite_to_clean_hls_cdn(realistic, "https://newcntv.qcloudcdn.com/asp/hls/main/x/main.m3u8")
    assert cleaned[0].url.startswith("https://newcntv.qcloudcdn.com/asp/hls/")


def test_encrypted_playlist_is_rejected():
    with pytest.raises(CctvError):
        parse_master_playlist("https://cdn.example/main.m3u8", "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128\n")


def test_filename_and_collision_handling(tmp_path: Path):
    assert safe_filename('新闻:/周刊*2026') == "新闻__周刊_2026"
    first = next_available_path(tmp_path, "节目")
    first.touch()
    assert next_available_path(tmp_path, "节目").name == "节目 (1).mp4"


def test_duration_parsing():
    assert parse_duration("00:43:47") == 2627
    assert parse_duration("2627.32") == 2627.32


def test_desktop_path_has_desktop_name():
    assert desktop_path().name.lower() == "desktop"
