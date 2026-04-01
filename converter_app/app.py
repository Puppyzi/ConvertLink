import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from converter_app import APP_NAME
from converter_app.downloader import (
    DependencyError,
    MediaInspectionResult,
    VideoQualityOption,
    dependency_report,
    download_media,
    human_readable_size,
    inspect_media,
)
from converter_app.utils import downloads_directory, ensure_directory, notify, reveal_in_file_manager


def _human_readable_duration(duration_seconds: Optional[int]) -> str:
    if not duration_seconds:
        return "unknown duration"

    minutes, seconds = divmod(duration_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


class InspectWorker(QObject):
    progress = Signal(str)
    success = Signal(object)
    failure = Signal(str)
    completed = Signal()

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    @Slot()
    def run(self) -> None:
        try:
            result = inspect_media(self.url, progress_callback=self.progress.emit)
            self.success.emit(result)
        except DependencyError as exc:
            self.failure.emit(str(exc))
        except Exception as exc:
            self.failure.emit(str(exc))
        finally:
            self.completed.emit()


class DownloadWorker(QObject):
    progress = Signal(str)
    progress_value = Signal(int)
    phase = Signal(str)
    success = Signal(str)
    failure = Signal(str)
    completed = Signal()

    def __init__(
        self,
        url: str,
        output_format: str,
        output_dir: Path,
        mp4_option: Optional[VideoQualityOption] = None,
    ) -> None:
        super().__init__()
        self.url = url
        self.output_format = output_format
        self.output_dir = output_dir
        self.mp4_option = mp4_option

    @Slot()
    def run(self) -> None:
        try:
            result = download_media(
                url=self.url,
                output_format=self.output_format,
                output_dir=self.output_dir,
                progress_callback=self.progress.emit,
                progress_value_callback=self.progress_value.emit,
                phase_callback=self.phase.emit,
                mp4_selector=self.mp4_option.selector if self.mp4_option else None,
                mp4_label=self.mp4_option.label if self.mp4_option else None,
            )
            self.success.emit(str(result.file_path))
        except DependencyError as exc:
            self.failure.emit(str(exc))
        except Exception as exc:
            self.failure.emit(str(exc))
        finally:
            self.completed.emit()


class ConverterWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.output_dir = ensure_directory(downloads_directory())
        self.last_output_file: Optional[Path] = None
        self.worker_thread: Optional[QThread] = None
        self.worker: Optional[DownloadWorker] = None
        self.inspect_thread: Optional[QThread] = None
        self.inspect_worker: Optional[InspectWorker] = None
        self.quality_options: list[VideoQualityOption] = []
        self.analyzed_url = ""
        self.analyzed_title = ""
        self.analyzed_duration_seconds: Optional[int] = None

        self.setWindowTitle(APP_NAME)
        self.resize(1000, 700)
        self.setMinimumSize(780, 560)

        self.url_input: QLineEdit
        self.mp3_radio: QRadioButton
        self.mp4_radio: QRadioButton
        self.convert_button: QPushButton
        self.analyze_button: QPushButton
        self.quality_combo: QComboBox
        self.media_summary_label: QLabel
        self.quality_estimate_label: QLabel
        self.quality_panel: QWidget
        self.show_last_file_button: QPushButton
        self.dependency_label: QLabel
        self.status_label: QLabel
        self.progress_bar: QProgressBar
        self.log_box: QPlainTextEdit

        self._build_ui()
        self._bind_shortcuts()
        self._refresh_dependency_status()
        self._set_status("Ready. Finished files will be saved to Downloads.")
        self._update_format_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: #f7f4ee;
            }
            QLabel#title {
                color: #1c1b19;
                font-size: 30px;
                font-weight: 700;
            }
            QLabel#body {
                color: #4a443d;
                font-size: 13px;
            }
            QFrame#card {
                background: #fffaf2;
                border: 1px solid #ebdfd0;
                border-radius: 22px;
            }
            QLabel#section {
                color: #1c1b19;
                font-size: 14px;
                font-weight: 700;
            }
            QLineEdit, QComboBox {
                background: white;
                border: 1px solid #d9ccb8;
                border-radius: 12px;
                color: #1c1b19;
                font-size: 14px;
                padding: 12px 14px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #2f6fed;
            }
            QComboBox::drop-down {
                border: none;
                width: 28px;
            }
            QComboBox QAbstractItemView {
                background: white;
                border: 1px solid #d9ccb8;
                selection-background-color: #2f6fed;
                selection-color: white;
            }
            QRadioButton {
                color: #1c1b19;
                font-size: 14px;
                spacing: 8px;
            }
            QPushButton {
                background: #efe5d8;
                border: none;
                border-radius: 12px;
                color: #1c1b19;
                font-size: 13px;
                font-weight: 600;
                padding: 11px 16px;
            }
            QPushButton:hover {
                background: #e8dbc8;
            }
            QPushButton:disabled {
                background: #ddd6cb;
                color: #746d65;
            }
            QPushButton#primary {
                background: #2f6fed;
                color: white;
            }
            QPushButton#primary:hover {
                background: #245ed0;
            }
            QProgressBar {
                background: #ebdfd0;
                border: none;
                border-radius: 9px;
                color: #1c1b19;
                min-height: 18px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: #2f6fed;
                border-radius: 9px;
            }
            QPlainTextEdit {
                background: #201d19;
                border: none;
                border-radius: 18px;
                color: #f3efe7;
                padding: 14px;
                selection-background-color: #d9733a;
            }
            """
        )

        container = QWidget()
        root_layout = QVBoxLayout(container)
        root_layout.setContentsMargins(24, 24, 24, 24)
        root_layout.setSpacing(18)

        header_layout = QVBoxLayout()
        header_layout.setSpacing(6)

        title = QLabel(APP_NAME)
        title.setObjectName("title")
        title.setAlignment(Qt.AlignHCenter)

        body = QLabel(
            "Paste a video link, choose MP3 or MP4, and save it straight into Downloads."
        )
        body.setObjectName("body")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignHCenter)

        header_layout.addWidget(title)
        header_layout.addWidget(body)
        root_layout.addLayout(header_layout)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 22, 22, 22)
        card_layout.setSpacing(16)

        link_label = QLabel("Video Link")
        link_label.setObjectName("section")
        card_layout.addWidget(link_label)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste a YouTube or other supported video link")
        self.url_input.returnPressed.connect(self.start_download)
        self.url_input.textChanged.connect(self._handle_url_change)
        card_layout.addWidget(self.url_input)

        format_label = QLabel("Output Format")
        format_label.setObjectName("section")
        card_layout.addWidget(format_label)

        radio_row = QHBoxLayout()
        radio_row.setSpacing(20)

        self.mp3_radio = QRadioButton("MP3 audio")
        self.mp4_radio = QRadioButton("MP4 video")
        self.mp3_radio.setChecked(True)
        self.mp3_radio.toggled.connect(self._update_format_ui)
        self.mp4_radio.toggled.connect(self._update_format_ui)

        self.format_group = QButtonGroup(self)
        self.format_group.addButton(self.mp3_radio)
        self.format_group.addButton(self.mp4_radio)

        radio_row.addWidget(self.mp3_radio)
        radio_row.addWidget(self.mp4_radio)
        radio_row.addStretch(1)
        card_layout.addLayout(radio_row)

        self.quality_panel = QWidget()
        quality_layout = QVBoxLayout(self.quality_panel)
        quality_layout.setContentsMargins(0, 0, 0, 0)
        quality_layout.setSpacing(10)

        quality_label = QLabel("MP4 Quality")
        quality_label.setObjectName("section")
        quality_layout.addWidget(quality_label)

        analysis_row = QHBoxLayout()
        analysis_row.setSpacing(10)

        self.analyze_button = QPushButton("Check MP4 Sizes")
        self.analyze_button.clicked.connect(self.inspect_mp4_options)
        analysis_row.addWidget(self.analyze_button)

        self.quality_combo = QComboBox()
        self.quality_combo.setPlaceholderText("Check MP4 sizes to load available qualities")
        self.quality_combo.currentIndexChanged.connect(self._update_quality_summary)
        analysis_row.addWidget(self.quality_combo, 1)
        quality_layout.addLayout(analysis_row)

        self.media_summary_label = QLabel()
        self.media_summary_label.setObjectName("body")
        self.media_summary_label.setWordWrap(True)
        quality_layout.addWidget(self.media_summary_label)

        self.quality_estimate_label = QLabel()
        self.quality_estimate_label.setObjectName("body")
        self.quality_estimate_label.setWordWrap(True)
        quality_layout.addWidget(self.quality_estimate_label)

        card_layout.addWidget(self.quality_panel)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)

        self.convert_button = QPushButton("Convert and Download")
        self.convert_button.setObjectName("primary")
        self.convert_button.clicked.connect(self.start_download)

        open_downloads_button = QPushButton("Open Downloads")
        open_downloads_button.clicked.connect(self.open_downloads_folder)

        fullscreen_button = QPushButton("Toggle Fullscreen")
        fullscreen_button.clicked.connect(self.toggle_fullscreen)

        self.show_last_file_button = QPushButton("Show Last File")
        self.show_last_file_button.setEnabled(False)
        self.show_last_file_button.clicked.connect(self.show_last_file)

        button_row.addWidget(self.convert_button)
        button_row.addWidget(open_downloads_button)
        button_row.addWidget(fullscreen_button)
        button_row.addStretch(1)
        button_row.addWidget(self.show_last_file_button)
        card_layout.addLayout(button_row)

        root_layout.addWidget(card)

        self.dependency_label = QLabel()
        self.dependency_label.setObjectName("body")
        self.dependency_label.setWordWrap(True)
        root_layout.addWidget(self.dependency_label)

        self.status_label = QLabel()
        self.status_label.setObjectName("body")
        self.status_label.setWordWrap(True)
        root_layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Ready")
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()
        root_layout.addWidget(self.progress_bar)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFont(QFont("Menlo", 11))
        root_layout.addWidget(self.log_box, 1)

        self.setCentralWidget(container)
        self.url_input.setFocus()

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence("F11"), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence("Escape"), self, activated=self.exit_fullscreen)

    def _current_url(self) -> str:
        return self.url_input.text().strip()

    def _selected_format(self) -> str:
        return "mp3" if self.mp3_radio.isChecked() else "mp4"

    def _selected_quality_option(self) -> Optional[VideoQualityOption]:
        if self.quality_combo.count() == 0:
            return None
        data = self.quality_combo.currentData()
        return data if isinstance(data, VideoQualityOption) else None

    def _has_current_quality_selection(self) -> bool:
        return (
            self._selected_format() != "mp4"
            or (
                self.analyzed_url == self._current_url()
                and self._selected_quality_option() is not None
            )
        )

    def _is_busy(self) -> bool:
        return self.worker_thread is not None or self.inspect_thread is not None

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def _show_indeterminate_progress(self, label: str) -> None:
        self.progress_bar.show()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat(label)

    def _show_progress_value(self, value: int, label: Optional[str] = None) -> None:
        normalized_value = max(0, min(100, value))
        self.progress_bar.show()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(normalized_value)
        self.progress_bar.setFormat(label or f"{normalized_value}%")

    def _reset_progress(self, label: str = "Ready") -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(label)
        self.progress_bar.hide()

    def _append_log(self, message: str) -> None:
        if message.startswith("__FINAL_PATH__:"):
            return
        self.log_box.appendPlainText(message)
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum()
        )

    def _clear_log(self) -> None:
        self.log_box.clear()

    def _refresh_dependency_status(self) -> None:
        status = dependency_report()
        missing_tools: list[str] = []
        if not status["yt_dlp"]:
            missing_tools.append("yt-dlp")
        if not status["deno"]:
            missing_tools.append("Deno")
        if not status["ffmpeg"]:
            missing_tools.append("FFmpeg")

        if missing_tools:
            if len(missing_tools) == 1:
                missing_text = missing_tools[0]
                verb = "is"
            elif len(missing_tools) == 2:
                missing_text = " and ".join(missing_tools)
                verb = "are"
            else:
                missing_text = ", ".join(missing_tools[:-1]) + f", and {missing_tools[-1]}"
                verb = "are"

            self.dependency_label.setText(
                f"Setup needed: {missing_text} {verb} missing. Run './setup_tools.sh' to restore the bundled tools."
            )
            return

        self.dependency_label.setText(
            "Tip: For MP4, click 'Check MP4 Sizes' to choose a quality and preview the file size before downloading."
        )

    def _preferred_quality_index(self) -> int:
        for target_height in (720, 1080, 480):
            for index, option in enumerate(self.quality_options):
                if option.height == target_height:
                    return index
        return 0

    def _reset_quality_state(self) -> None:
        self.quality_options = []
        self.analyzed_url = ""
        self.analyzed_title = ""
        self.analyzed_duration_seconds = None
        self.quality_combo.blockSignals(True)
        self.quality_combo.clear()
        self.quality_combo.blockSignals(False)
        self.media_summary_label.setText(
            "Analyze this link to see which MP4 resolutions are actually available."
        )
        self.quality_estimate_label.setText(
            "Estimated MP4 size will appear here before download."
        )

    @Slot()
    def _update_format_ui(self) -> None:
        is_mp4 = self._selected_format() == "mp4"
        self.quality_panel.setVisible(is_mp4)
        self._update_action_states()
        if is_mp4 and not self._has_current_quality_selection():
            self._set_status(
                "Choose MP4 mode, then click 'Check MP4 Sizes' to load qualities and estimated download size."
            )

    def _update_action_states(self) -> None:
        busy = self._is_busy()
        is_mp4 = self._selected_format() == "mp4"
        url_present = bool(self._current_url())
        can_download = url_present and not busy and (
            not is_mp4 or self._has_current_quality_selection()
        )

        self.url_input.setEnabled(not busy)
        self.mp3_radio.setEnabled(not busy)
        self.mp4_radio.setEnabled(not busy)
        self.convert_button.setEnabled(can_download)
        self.analyze_button.setEnabled(not busy and is_mp4 and url_present)
        self.quality_combo.setEnabled(not busy and is_mp4 and bool(self.quality_options))

    @Slot(str)
    def _handle_url_change(self, _text: str) -> None:
        self._reset_quality_state()
        self._update_format_ui()
        if self._selected_format() == "mp4" and self._current_url():
            self._set_status(
                "Link updated. Click 'Check MP4 Sizes' to refresh available video qualities."
            )
        elif self._selected_format() == "mp3" and self._current_url():
            self._set_status(f"Ready. Converted files will be saved to {self.output_dir}.")

    @Slot()
    def inspect_mp4_options(self) -> None:
        if self.inspect_thread is not None or self.worker_thread is not None:
            return

        url = self._current_url()
        if not url:
            QMessageBox.warning(self, APP_NAME, "Paste a video link first.")
            return

        self._refresh_dependency_status()
        self._clear_log()
        self._append_log("Inspecting MP4 qualities for this link...")
        self._set_status("Fetching available MP4 qualities and estimated sizes...")
        self._show_indeterminate_progress("Checking MP4 sizes...")

        self.inspect_thread = QThread(self)
        self.inspect_worker = InspectWorker(url)
        self.inspect_worker.moveToThread(self.inspect_thread)

        self.inspect_thread.started.connect(self.inspect_worker.run)
        self.inspect_worker.progress.connect(self._append_log)
        self.inspect_worker.success.connect(self._handle_inspection_success)
        self.inspect_worker.failure.connect(self._handle_inspection_error)
        self.inspect_worker.completed.connect(self.inspect_thread.quit)
        self.inspect_worker.completed.connect(self.inspect_worker.deleteLater)
        self.inspect_thread.finished.connect(self._reset_inspection_state)
        self.inspect_thread.finished.connect(self.inspect_thread.deleteLater)
        self.inspect_thread.start()
        self._update_action_states()

    @Slot()
    def _reset_inspection_state(self) -> None:
        self.inspect_thread = None
        self.inspect_worker = None
        self._update_action_states()

    @Slot(object)
    def _handle_inspection_success(self, result: object) -> None:
        if not isinstance(result, MediaInspectionResult):
            self._handle_inspection_error("Unexpected inspection result.")
            return

        if result.source_url != self._current_url():
            self._set_status("Link changed while loading qualities. Please check MP4 sizes again.")
            return

        self.quality_options = result.mp4_options
        self.analyzed_url = result.source_url
        self.analyzed_title = result.title
        self.analyzed_duration_seconds = result.duration_seconds

        self.quality_combo.blockSignals(True)
        self.quality_combo.clear()
        for option in self.quality_options:
            self.quality_combo.addItem(option.label, option)
        self.quality_combo.blockSignals(False)

        default_index = self._preferred_quality_index()
        self.quality_combo.setCurrentIndex(default_index)
        self._update_quality_summary()

        summary = (
            f"Loaded {len(self.quality_options)} MP4 qualities for "
            f"'{self.analyzed_title}' ({_human_readable_duration(self.analyzed_duration_seconds)})."
        )
        self.media_summary_label.setText(summary)
        self._set_status("MP4 quality options are ready. Pick one and then download.")
        self._reset_progress("Qualities loaded")

        for option in self.quality_options:
            size_text = human_readable_size(option.estimated_size_bytes)
            self._append_log(f"Option: {option.label} | Estimated size: {size_text}")

        self._update_action_states()

    @Slot(str)
    def _handle_inspection_error(self, error_message: str) -> None:
        self._reset_quality_state()
        self._set_status("Could not load MP4 quality options. Check the log for details.")
        self._reset_progress("Inspection failed")
        self._append_log("")
        self._append_log("Inspection error:")
        self._append_log(error_message)
        self._update_action_states()
        QMessageBox.critical(self, APP_NAME, error_message)

    @Slot()
    def start_download(self) -> None:
        if self.worker_thread is not None or self.inspect_thread is not None:
            return

        url = self._current_url()
        if not url:
            QMessageBox.warning(self, APP_NAME, "Paste a video link first.")
            return

        selected_option = None
        if self._selected_format() == "mp4":
            if self.analyzed_url != url or not self._selected_quality_option():
                QMessageBox.information(
                    self,
                    APP_NAME,
                    "Click 'Check MP4 Sizes' first so you can choose a resolution and see its estimated file size before downloading.",
                )
                return
            selected_option = self._selected_quality_option()

        self._refresh_dependency_status()
        self._clear_log()
        self._set_status("Starting download...")
        self._append_log(f"Saving output into {self.output_dir}")
        self._show_progress_value(0, "Starting...")
        if selected_option:
            size_text = human_readable_size(selected_option.estimated_size_bytes)
            self._append_log(
                f"Chosen MP4 quality: {selected_option.label} | Estimated size: {size_text}"
            )

        self.worker_thread = QThread(self)
        self.worker = DownloadWorker(
            url=url,
            output_format=self._selected_format(),
            output_dir=self.output_dir,
            mp4_option=selected_option,
        )
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._append_log)
        self.worker.progress_value.connect(self._update_download_progress)
        self.worker.phase.connect(self._update_progress_phase)
        self.worker.success.connect(self._handle_success)
        self.worker.failure.connect(self._handle_error)
        self.worker.completed.connect(self.worker_thread.quit)
        self.worker.completed.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self._reset_worker_state)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()
        self._update_action_states()

    @Slot()
    def _reset_worker_state(self) -> None:
        self.worker_thread = None
        self.worker = None
        self._update_action_states()

    @Slot(int)
    def _update_quality_summary(self, _index: int = -1) -> None:
        option = self._selected_quality_option()
        if not option:
            self.quality_estimate_label.setText(
                "Estimated MP4 size will appear here before download."
            )
            self._update_action_states()
            return

        size_text = human_readable_size(option.estimated_size_bytes)
        detail_bits = [f"Selected: {option.label}"]
        if option.estimated_size_bytes is not None:
            detail_bits.append(f"Estimated size: {size_text}")
        else:
            detail_bits.append("Estimated size: unavailable")
        detail_bits.append(f"Delivery: {option.source_note}")
        self.quality_estimate_label.setText(" | ".join(detail_bits))
        self._update_action_states()

    @Slot(int)
    def _update_download_progress(self, value: int) -> None:
        if value >= 100:
            self._show_progress_value(100, "Finishing...")
            return
        self._show_progress_value(value)

    @Slot(str)
    def _update_progress_phase(self, phase: str) -> None:
        if phase == "download":
            if self.progress_bar.maximum() == 0:
                self._show_progress_value(max(self.progress_bar.value(), 0), "Downloading...")
            return

        if phase == "Complete":
            self._show_progress_value(100, "Complete")
            return

        self._show_indeterminate_progress(phase)

    @Slot(str)
    def _handle_success(self, file_path_str: str) -> None:
        self._refresh_dependency_status()
        self.last_output_file = Path(file_path_str)
        self.show_last_file_button.setEnabled(True)
        self._set_status(f"Conversion Finished. Saved: {self.last_output_file}")
        self._show_progress_value(100, "Conversion Finished")
        notify(APP_NAME, f"Conversion Finished: {self.last_output_file.name}")
        QMessageBox.information(
            self,
            APP_NAME,
            f"Conversion Finished.\n\nSaved to:\n{self.last_output_file}",
        )

    @Slot(str)
    def _handle_error(self, error_message: str) -> None:
        self._refresh_dependency_status()
        self._set_status("Conversion failed. Check the log for details.")
        self._reset_progress("Failed")
        self._append_log("")
        self._append_log("Error:")
        self._append_log(error_message)
        QMessageBox.critical(self, APP_NAME, error_message)

    @Slot()
    def open_downloads_folder(self) -> None:
        reveal_in_file_manager(self.output_dir)

    @Slot()
    def show_last_file(self) -> None:
        if self.last_output_file and self.last_output_file.exists():
            reveal_in_file_manager(self.last_output_file)

    @Slot()
    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    @Slot()
    def exit_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()


def run() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = ConverterWindow()
    window.show()
    sys.exit(app.exec())
