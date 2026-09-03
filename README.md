# CCTV 新闻周刊下载器

一个 Windows 桌面工具和命令行工具，用于读取央视《新闻周刊》最近节目，显示实际可下载的清晰度，并调用 FFmpeg 输出 MP4。

公开仓库：<https://github.com/SacrEllfarch/cctv-news-weekly-mvp>

## 直接使用桌面版

推荐使用便携包 `dist\\CCTVNewsWeekly-windows.zip`。完整解压后双击 `CCTVNewsWeekly\\CCTVNewsWeekly.exe`。不要只复制 EXE，它需要同目录的 `_internal` 文件夹和内置 FFmpeg/ffprobe。

桌面版会自动加载最近 20 期节目，点击节目后探测实际分辨率，只显示真实可下载的清晰度，默认保存到 Windows 桌面，并在后台显示下载进度。

## 从源码运行

需要 Windows、Python 3.11+ 和网络连接：

```powershell
cd F:\tech
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements-desktop.txt
.\.venv\Scripts\python.exe .\desktop_app.py
```

也可以双击 `start_desktop.cmd`。如果直接运行 `desktop_app.py` 出现 `QtCore` DLL 错误，说明使用了错误的 Python 解释器，请使用项目 `.venv`。

## 命令行用法

```powershell
# 列出最新节目和实际清晰度
.\.venv\Scripts\python.exe .\cctv_news_weekly.py --list

# 下载最高可用清晰度
.\.venv\Scripts\python.exe .\cctv_news_weekly.py --output-dir .\downloads

# 指定清晰度或码率
.\.venv\Scripts\python.exe .\cctv_news_weekly.py --quality 850 --output-dir .\downloads

# 下载前 8 秒进行测试
.\.venv\Scripts\python.exe .\cctv_news_weekly.py --quality 450 --max-seconds 8 --output .\downloads\smoke.mp4
```

## 构建便携版

```powershell
.\build_desktop.ps1
```

输出为 `dist\\CCTVNewsWeekly-windows.zip`。构建脚本会安装固定版本的 PySide6、PyInstaller 和测试依赖，并从 PATH 复制 FFmpeg/ffprobe。桌面依赖固定为 PySide6 6.8.3，以避免部分 Windows 环境加载 Qt 6.11 DLL 失败。

## 清晰度和下载说明

央视接口中的码率和分辨率是播放列表的名义值，不一定等于实际下载流。程序使用 ffprobe 探测真实分辨率，并合并重复档位；没有真实 1280×720 流时不会显示“高清”或“超清”。

部分 H5 CDN 节点可能返回损坏 TS 分片，程序会优先使用接口提供的常规公开 HLS CDN，并拒绝受保护或加密播放列表。

## 开发检查

```powershell
.\.venv\Scripts\python.exe -m pytest .\tests -q
```

测试覆盖 JSONP/播放列表解析、清晰度对齐、加密流拒绝、文件重名、桌面路径和 Windows 隐藏子进程。

## 版权和第三方组件

程序只处理央视接口标记为公开且未加密的内容。请仅下载你有权保存的内容，并遵守央视网站条款和版权要求。

PySide6、PyInstaller、FFmpeg/ffprobe 的许可证信息见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。FFmpeg 二进制不提交到 Git 仓库。
