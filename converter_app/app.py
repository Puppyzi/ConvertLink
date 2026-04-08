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
    normalize_media_url,
)
from converter_app.utils import downloads_directory, ensure_directory, notify, reveal_in_file_manager
from converter_app.utils import open_media_file


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


SOURCE_UI_COPY = {
    "youtube": {
        "display_name": "YouTube",
        "body": "Paste a YouTube link, choose MP3 or MP4, and save it straight into Downloads.",
        "link_label": "YouTube Link",
        "placeholder": "Paste a YouTube video link",
        "quality_label": "YouTube MP4 Quality",
        "quality_combo_placeholder": "Check MP4 sizes to load available qualities",
        "empty_summary": "Analyze this YouTube link to see which MP4 resolutions are actually available.",
        "tip": "Tip: For MP4, click 'Check MP4 Sizes' to choose a quality and preview the file size before downloading.",
        "mp4_prompt": "Choose MP4 mode, then click 'Check MP4 Sizes' to load qualities and estimated download size.",
        "refresh_prompt": "Link updated. Click 'Check MP4 Sizes' to refresh available video qualities.",
    },
    "twitter": {
        "display_name": "X/Twitter",
        "body": "Paste an X/Twitter post link, choose MP3 or MP4, and save it straight into Downloads.",
        "link_label": "X/Twitter Link",
        "placeholder": "Paste an X/Twitter post link",
        "quality_label": "X/Twitter MP4 Quality",
        "quality_combo_placeholder": "Check MP4 sizes to load available X/Twitter variants",
        "empty_summary": "Analyze this X/Twitter post to see which MP4 sizes are actually available.",
        "tip": "Tip: For X/Twitter MP4, click 'Check MP4 Sizes' to load available sizes. MP3 works when the post includes audio.",
        "mp4_prompt": "Choose MP4 mode, then click 'Check MP4 Sizes' to load X/Twitter sizes and estimated download size.",
        "refresh_prompt": "Link updated. Click 'Check MP4 Sizes' to refresh available X/Twitter video sizes.",
    },
    "instagram": {
        "display_name": "Instagram",
        "body": "Paste an Instagram reel or post link, choose MP3 or MP4, and save it straight into Downloads.",
        "link_label": "Instagram Link",
        "placeholder": "Paste an Instagram reel or post link",
        "quality_label": "Instagram MP4 Quality",
        "quality_combo_placeholder": "Check MP4 sizes to load available Instagram variants",
        "empty_summary": "Analyze this Instagram link to see which MP4 sizes are actually available.",
        "tip": "Tip: For Instagram MP4, click 'Check MP4 Sizes' to load available sizes. MP3 works when the reel or post exposes audio.",
        "mp4_prompt": "Choose MP4 mode, then click 'Check MP4 Sizes' to load Instagram sizes and estimated download size.",
        "refresh_prompt": "Link updated. Click 'Check MP4 Sizes' to refresh available Instagram video sizes.",
    },
}


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
        self.source_mode = "youtube"
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
        self.resize(1050, 750)
        self.setMinimumSize(780, 560)

        self.body_label: QLabel
        self.youtube_source_button: QPushButton
        self.twitter_source_button: QPushButton
        self.instagram_source_button: QPushButton
        self.link_label: QLabel
        self.url_input: QLineEdit
        self.mp3_radio: QRadioButton
        self.mp4_radio: QRadioButton
        self.convert_button: QPushButton
        self.analyze_button: QPushButton
        self.quality_combo: QComboBox
        self.quality_section_label: QLabel
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
        self._reset_quality_state()
        self._refresh_dependency_status()
        self._set_status(f"Ready. Converted files will be saved to {self.output_dir}.")
        self._update_format_ui()

    def _build_ui(self) -> None:
        profile = self._source_profile()
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
            QPushButton#sourceButton {
                background: #efe5d8;
                border: 1px solid #e2d7c8;
                border-radius: 14px;
                max-width: 112px;
                min-width: 0px;
                padding: 8px 8px;
            }
            QPushButton#sourceButton:hover {
                background: #e8dbc8;
            }
            QPushButton#sourceButton:checked {
                background: #2f6fed;
                border: 1px solid #2f6fed;
                color: white;
            }
            QPushButton#sourceButton:checked:hover {
                background: #245ed0;
                border: 1px solid #245ed0;
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

        self.body_label = QLabel(profile["body"])
        self.body_label.setObjectName("body")
        self.body_label.setWordWrap(True)
        self.body_label.setAlignment(Qt.AlignHCenter)

        header_layout.addWidget(title)
        header_layout.addWidget(self.body_label)
        root_layout.addLayout(header_layout)

        source_row = QHBoxLayout()
        source_row.setSpacing(12)
        source_row.addStretch(1)

        self.youtube_source_button = QPushButton("YouTube")
        self.youtube_source_button.setObjectName("sourceButton")
        self.youtube_source_button.setCheckable(True)
        self.youtube_source_button.setChecked(True)
        self.youtube_source_button.clicked.connect(
            lambda _checked=False: self._set_source_mode("youtube")
        )

        self.twitter_source_button = QPushButton("X/Twitter")
        self.twitter_source_button.setObjectName("sourceButton")
        self.twitter_source_button.setCheckable(True)
        self.twitter_source_button.clicked.connect(
            lambda _checked=False: self._set_source_mode("twitter")
        )

        self.instagram_source_button = QPushButton("Instagram")
        self.instagram_source_button.setObjectName("sourceButton")
        self.instagram_source_button.setCheckable(True)
        self.instagram_source_button.clicked.connect(
            lambda _checked=False: self._set_source_mode("instagram")
        )

        self.source_button_group = QButtonGroup(self)
        self.source_button_group.setExclusive(True)
        self.source_button_group.addButton(self.youtube_source_button)
        self.source_button_group.addButton(self.twitter_source_button)
        self.source_button_group.addButton(self.instagram_source_button)

        source_row.addWidget(self.youtube_source_button)
        source_row.addWidget(self.twitter_source_button)
        source_row.addWidget(self.instagram_source_button)
        source_row.addStretch(1)
        root_layout.addLayout(source_row)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 22, 22, 22)
        card_layout.setSpacing(16)

        self.link_label = QLabel(profile["link_label"])
        self.link_label.setObjectName("section")
        card_layout.addWidget(self.link_label)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(profile["placeholder"])
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

        self.quality_section_label = QLabel(profile["quality_label"])
        self.quality_section_label.setObjectName("section")
        quality_layout.addWidget(self.quality_section_label)

        analysis_row = QHBoxLayout()
        analysis_row.setSpacing(10)

        self.analyze_button = QPushButton("Check MP4 Sizes")
        self.analyze_button.clicked.connect(self.inspect_mp4_options)
        analysis_row.addWidget(self.analyze_button)

        self.quality_combo = QComboBox()
        self.quality_combo.setPlaceholderText(profile["quality_combo_placeholder"])
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

    def _normalized_current_url(self) -> str:
        current = self._current_url()
        return normalize_media_url(current) if current else ""

    def _source_profile(self) -> dict[str, str]:
        return SOURCE_UI_COPY[self.source_mode]

    def _source_display_name(self) -> str:
        return self._source_profile()["display_name"]

    def _apply_source_copy(self) -> None:
        profile = self._source_profile()
        self.body_label.setText(profile["body"])
        self.link_label.setText(profile["link_label"])
        self.url_input.setPlaceholderText(profile["placeholder"])
        self.quality_section_label.setText(profile["quality_label"])
        self.quality_combo.setPlaceholderText(profile["quality_combo_placeholder"])

    def _set_source_mode(self, source_mode: str) -> None:
        if source_mode not in SOURCE_UI_COPY or source_mode == self.source_mode:
            return

        self.source_mode = source_mode
        self.youtube_source_button.setChecked(source_mode == "youtube")
        self.twitter_source_button.setChecked(source_mode == "twitter")
        self.instagram_source_button.setChecked(source_mode == "instagram")
        self._apply_source_copy()
        self._reset_quality_state()
        self._refresh_dependency_status()
        self._update_format_ui()

        if self._current_url():
            if self._selected_format() == "mp4":
                self._set_status(self._source_profile()["refresh_prompt"])
            else:
                self._set_status(
                    f"Ready. Converted files will be saved to {self.output_dir}."
                )
            return

        self._set_status(
            f"{self._source_display_name()} mode selected. Paste a link to get started."
        )

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
                self.analyzed_url == self._normalized_current_url()
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
            self._source_profile()["tip"]
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
        self.media_summary_label.setText(self._source_profile()["empty_summary"])
        self.quality_estimate_label.setText(
            "Estimated MP4 size will appear here before download."
        )

    @Slot()
    def _update_format_ui(self) -> None:
        is_mp4 = self._selected_format() == "mp4"
        self.quality_panel.setVisible(is_mp4)
        self._update_action_states()
        if is_mp4 and not self._has_current_quality_selection():
            self._set_status(self._source_profile()["mp4_prompt"])

    def _update_action_states(self) -> None:
        busy = self._is_busy()
        is_mp4 = self._selected_format() == "mp4"
        url_present = bool(self._current_url())
        can_download = url_present and not busy and (
            not is_mp4 or self._has_current_quality_selection()
        )

        self.url_input.setEnabled(not busy)
        self.youtube_source_button.setEnabled(not busy)
        self.twitter_source_button.setEnabled(not busy)
        self.instagram_source_button.setEnabled(not busy)
        self.mp3_radio.setEnabled(not busy)
        self.mp4_radio.setEnabled(not busy)
        self.convert_button.setEnabled(can_download)
        self.analyze_button.setEnabled(not busy and is_mp4 and url_present)
        self.quality_combo.setEnabled(not busy and is_mp4 and bool(self.quality_options))

    def _refresh_last_file_button(self) -> None:
        last_file = self.last_output_file
        if not last_file:
            self.show_last_file_button.setText("Show Last File")
            self.show_last_file_button.setEnabled(False)
            return

        if last_file.suffix.lower() == ".mp3":
            self.show_last_file_button.setText("Play Last File")
        else:
            self.show_last_file_button.setText("Show Last File")

        self.show_last_file_button.setEnabled(last_file.exists())

    @Slot(str)
    def _handle_url_change(self, _text: str) -> None:
        self._reset_quality_state()
        self._update_format_ui()
        if self._selected_format() == "mp4" and self._current_url():
            self._set_status(self._source_profile()["refresh_prompt"])
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
        self._append_log(
            f"Inspecting {self._source_display_name()} MP4 qualities for this link..."
        )
        self._set_status(
            f"Fetching available {self._source_display_name()} MP4 qualities and estimated sizes..."
        )
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

        if result.source_url != self._normalized_current_url():
            self._set_status("Link changed while loading qualities. Please check MP4 sizes again.")
            return

        self.quality_options = result.mp4_options
        self.analyzed_url = normalize_media_url(result.source_url)
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
            f"Loaded {len(self.quality_options)} {self._source_display_name()} MP4 qualities for "
            f"'{self.analyzed_title}' ({_human_readable_duration(self.analyzed_duration_seconds)})."
        )
        self.media_summary_label.setText(summary)
        self._set_status(
            f"{self._source_display_name()} MP4 quality options are ready. Pick one and then download."
        )
        self._reset_progress("Qualities loaded")

        for option in self.quality_options:
            size_text = human_readable_size(option.estimated_size_bytes)
            self._append_log(f"Option: {option.label} | Estimated size: {size_text}")

        self._update_action_states()

    @Slot(str)
    def _handle_inspection_error(self, error_message: str) -> None:
        self._reset_quality_state()
        self._set_status(
            f"Could not load {self._source_display_name()} MP4 quality options. Check the log for details."
        )
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
            if self.analyzed_url != self._normalized_current_url() or not self._selected_quality_option():
                QMessageBox.information(
                    self,
                    APP_NAME,
                    f"Click 'Check MP4 Sizes' first so you can choose a {self._source_display_name()} MP4 option and see its estimated file size before downloading.",
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
        self._refresh_last_file_button()
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
            if self.last_output_file.suffix.lower() == ".mp3":
                open_media_file(self.last_output_file)
                return
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
