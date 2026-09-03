# CCTV 新闻周刊下载 MVP

一个 Python/PySide6 工具，用于读取央视《新闻周刊》栏目页的最新节目，解析公开视频接口返回的 HLS 清晰度，并调用 FFmpeg 下载为 MP4。

## 环境

- Python 3.11+
- FFmpeg 8+，并确保 `ffmpeg` 在 PATH 中
- 网络可访问 `tv.cctv.com`、`vdn.apps.cntv.cn` 和央视 CDN

桌面版额外需要 PySide6；构建便携版需要 PyInstaller。运行 `build_desktop.ps1` 会在项目内 `.venv` 安装这些依赖。

## 用法

列出最新节目和清晰度：

```powershell
python .\cctv_news_weekly.py --list
```

以 JSON 输出，便于后续 GUI 或插件复用：

```powershell
python .\cctv_news_weekly.py --list --json
```

下载最高画质：

```powershell
python .\cctv_news_weekly.py --quality 超清 --output-dir .\downloads
```

下载前 10 秒做冒烟测试：

```powershell
python .\cctv_news_weekly.py --quality 450 --max-seconds 10 --output .\downloads\smoke.mp4
```

也可以指定某一期详情页：

```powershell
python .\cctv_news_weekly.py --episode-url "https://tv.cctv.com/2026/08/29/VIDEhVO6OefI3gyWQbc8N4Ew260829.shtml" --list
```

## 桌面版

直接启动窗口：

```powershell
.\.venv\Scripts\python.exe .\desktop_app.py
```

构建 Windows 便携包，FFmpeg 会从 PATH 复制进构建产物：

```powershell
.\build_desktop.ps1
```

产物为 `dist\CCTVNewsWeekly-windows.zip`。解压后双击 `CCTVNewsWeekly.exe` 即可使用；程序默认保存到桌面。

程序不绕过 DRM 或受保护视频；如果接口标记 `is_protected=1` 或 HLS 播放列表包含加密密钥，会直接停止。

## 当前实现

1. 调用央视栏目 JSONP 接口获取最近 20 期节目。
2. 从接口或详情页获取播放器 GUID。
3. 请求 `getHttpVideoInfo.do` 获取元数据和公开 HLS 播放列表。
4. 解析清晰度、码率、分辨率和相对分片地址。
5. 下载时优先使用接口返回的常规公开 HLS CDN；部分 H5 CDN 节点可能返回损坏 TS 分片，程序会改用同 GUID 的常规 HLS 路径。
6. 由 FFmpeg 合并 HLS 分片并输出 MP4，失败时保留临时文件。

仅建议下载你有权保存的内容，遵守央视网站条款和版权要求。
