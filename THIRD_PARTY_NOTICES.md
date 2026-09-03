# 第三方组件声明

桌面版使用 PySide6 和 PyInstaller 构建，并随便携包附带 FFmpeg/ffprobe 可执行文件。

构建发布包时，请记录 `ffmpeg -version` 输出，并将所用 FFmpeg 发行版的许可证文本一并放入发布压缩包。FFmpeg 的具体许可证取决于所使用的构建选项。FFmpeg 二进制不提交到本仓库。

PySide6 和 PyInstaller 的许可证及版权信息以其发行包中的 LICENSE/NOTICE 文件为准。
