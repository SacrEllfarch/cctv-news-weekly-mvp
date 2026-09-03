"""PySide6 desktop UI for the CCTV News Weekly downloader."""

from __future__ import annotations

import sys
import threading
import os
from pathlib import Path

# PyInstaller's onedir layout keeps Qt DLLs under _internal/PySide6. Register
# those directories before importing the extension modules on Windows.
if sys.platform == "win32":
    _bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for _dll_dir in (_bundle_root / "PySide6", _bundle_root / "shiboken6"):
        if _dll_dir.is_dir():
            os.add_dll_directory(str(_dll_dir))

try:
    from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import (
        QApplication, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
        QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
        QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    )
except (ImportError, OSError) as exc:
    raise SystemExit(
        "无法加载 PySide6。请使用项目虚拟环境启动：start_desktop.cmd，"
        "或先运行 build_desktop.ps1 安装桌面依赖。"
    ) from exc

from cctv_news_weekly_core import (
    CctvError, DownloadCanceled, Episode, ResolvedEpisode, StreamVariant,
    bundled_ffmpeg_path, bundled_ffprobe_path, desktop_path, download_variant, list_episodes, next_available_path,
    resolve_episode, safe_filename,
)


class ListSignals(QObject):
    loaded = Signal(object)
    error = Signal(str)


class ListWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = ListSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.loaded.emit(list_episodes(20))
        except Exception as exc:
            self.signals.error.emit(str(exc))


class ResolveSignals(QObject):
    loaded = Signal(object)
    error = Signal(str)


class ResolveWorker(QRunnable):
    def __init__(self, episode: Episode) -> None:
        super().__init__()
        self.episode = episode
        self.signals = ResolveSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.loaded.emit(resolve_episode(self.episode, timeout=12, ffprobe_path=bundled_ffprobe_path()))
        except Exception as exc:
            self.signals.error.emit(str(exc))


class DownloadSignals(QObject):
    progress = Signal(int)
    finished = Signal(str)
    canceled = Signal()
    error = Signal(str)


class DownloadWorker(QRunnable):
    def __init__(self, variant: StreamVariant, output: Path, duration: float) -> None:
        super().__init__()
        self.variant = variant
        self.output = output
        self.duration = duration
        self.cancel_event = threading.Event()
        self.signals = DownloadSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = download_variant(
                self.variant, self.output, self.signals.progress.emit,
                self.cancel_event, self.duration, bundled_ffmpeg_path(),
            )
            self.signals.finished.emit(str(result))
        except DownloadCanceled:
            self.signals.canceled.emit()
        except CctvError as exc:
            self.signals.error.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("央视新闻周刊下载器")
        self.resize(960, 600)
        self.pool = QThreadPool.globalInstance()
        self.episodes: list[Episode] = []
        self.resolved: ResolvedEpisode | None = None
        self.download_worker: DownloadWorker | None = None
        self.resolve_worker: ResolveWorker | None = None
        self.resolve_generation = 0
        self._build_ui()
        self.refresh_episodes()

    def _build_ui(self) -> None:
        root = QWidget(self)
        layout = QVBoxLayout(root)
        top = QHBoxLayout()
        self.refresh_button = QPushButton("刷新列表")
        self.refresh_button.clicked.connect(self.refresh_episodes)
        self.status_label = QLabel("正在加载节目列表…")
        top.addWidget(self.refresh_button)
        top.addWidget(self.status_label, 1)
        layout.addLayout(top)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["日期", "节目", "时长"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.episode_selected)
        layout.addWidget(self.table, 1)

        options = QHBoxLayout()
        options.addWidget(QLabel("清晰度"))
        self.quality_combo = QComboBox()
        self.quality_combo.setMinimumWidth(240)
        options.addWidget(self.quality_combo)
        options.addWidget(QLabel("保存位置"))
        self.path_edit = QLineEdit(str(desktop_path()))
        options.addWidget(self.path_edit, 1)
        self.browse_button = QPushButton("选择目录")
        self.browse_button.clicked.connect(self.choose_directory)
        options.addWidget(self.browse_button)
        layout.addLayout(options)

        actions = QHBoxLayout()
        self.download_button = QPushButton("开始下载")
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self.start_download)
        self.cancel_button = QPushButton("取消下载")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_download)
        self.progress = QProgressBar()
        actions.addWidget(self.download_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.progress, 1)
        layout.addLayout(actions)
        self.setCentralWidget(root)

    @Slot()
    def refresh_episodes(self) -> None:
        if self.download_worker:
            return
        self.refresh_button.setEnabled(False)
        self.status_label.setText("正在获取最近 20 期…")
        worker = ListWorker()
        worker.signals.loaded.connect(self.episodes_loaded)
        worker.signals.error.connect(self.operation_error)
        self.pool.start(worker)

    @Slot(object)
    def episodes_loaded(self, episodes: list[Episode]) -> None:
        self.episodes = episodes
        self.table.setRowCount(len(episodes))
        for row, episode in enumerate(episodes):
            self.table.setItem(row, 0, QTableWidgetItem(episode.date.strftime("%Y-%m-%d")))
            self.table.setItem(row, 1, QTableWidgetItem(episode.title))
            self.table.setItem(row, 2, QTableWidgetItem(episode.duration or "未知"))
        self.refresh_button.setEnabled(True)
        self.status_label.setText(f"已加载 {len(episodes)} 期")
        if episodes:
            self.table.selectRow(0)

    @Slot()
    def episode_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.episodes) or self.download_worker:
            return
        self.resolved = None
        self.resolve_generation += 1
        generation = self.resolve_generation
        self.quality_combo.clear()
        self.download_button.setEnabled(False)
        self.status_label.setText("正在解析视频清晰度…")
        worker = ResolveWorker(self.episodes[row])
        self.resolve_worker = worker
        worker.signals.loaded.connect(lambda resolved, expected=generation: self.episode_resolved(resolved, expected))
        worker.signals.error.connect(lambda message, expected=generation: self.resolve_error(message, expected))
        self.pool.start(worker)

    @Slot(object)
    def episode_resolved(self, resolved: ResolvedEpisode, generation: int | None = None) -> None:
        if generation is not None and generation != self.resolve_generation:
            return
        self.resolve_worker = None
        self.resolved = resolved
        self.quality_combo.clear()
        for variant in resolved.variants:
            bitrate = f"{variant.bandwidth / 1000:.1f} Kbps" if variant.bandwidth else ""
            self.quality_combo.addItem(f"{variant.quality} · {variant.resolution} · {bitrate}", variant)
        self.quality_combo.setCurrentIndex(max(0, self.quality_combo.count() - 1))
        self.download_button.setEnabled(True)
        self.status_label.setText("已准备下载")

    @Slot(str, int)
    def resolve_error(self, message: str, generation: int) -> None:
        if generation != self.resolve_generation:
            return
        self.resolve_worker = None
        self.operation_error(message)

    @Slot()
    def choose_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择保存目录", self.path_edit.text())
        if directory:
            self.path_edit.setText(directory)

    @Slot()
    def start_download(self) -> None:
        if not self.resolved or self.quality_combo.currentIndex() < 0:
            return
        directory = Path(self.path_edit.text()).expanduser()
        if not directory.is_dir():
            QMessageBox.warning(self, "保存位置无效", "请选择一个存在的目录。")
            return
        title = safe_filename(str(self.resolved.info.get("title") or self.resolved.episode.title))
        worker = DownloadWorker(self.quality_combo.currentData(), next_available_path(directory, title), self.resolved.duration_seconds)
        self.download_worker = worker
        worker.signals.progress.connect(self.progress.setValue)
        worker.signals.finished.connect(self.download_finished)
        worker.signals.canceled.connect(self.download_canceled)
        worker.signals.error.connect(self.operation_error)
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.refresh_button.setEnabled(False)
        self.browse_button.setEnabled(False)
        self.progress.setValue(0)
        self.status_label.setText("正在下载…")
        self.pool.start(worker)

    @Slot()
    def cancel_download(self) -> None:
        if self.download_worker:
            self.download_worker.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("正在取消下载…")

    @Slot(str)
    def download_finished(self, path: str) -> None:
        self.download_worker = None
        self._restore_controls()
        self.progress.setValue(100)
        self.status_label.setText(f"下载完成：{path}")

    @Slot()
    def download_canceled(self) -> None:
        self.download_worker = None
        self._restore_controls()
        self.progress.setValue(0)
        self.status_label.setText("下载已取消")

    @Slot(str)
    def operation_error(self, message: str) -> None:
        self.download_worker = None
        self._restore_controls()
        self.status_label.setText("操作失败")
        QMessageBox.critical(self, "操作失败", message)

    def _restore_controls(self) -> None:
        self.refresh_button.setEnabled(True)
        self.browse_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.download_button.setEnabled(self.resolved is not None)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.download_worker:
            answer = QMessageBox.question(self, "正在下载", "下载尚未完成，确定取消并退出吗？")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.download_worker.cancel_event.set()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
