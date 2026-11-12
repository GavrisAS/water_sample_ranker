"""LoaderWindow interaction adapted from gdis_loader (AUTHOR_REDACTED)."""

import json
import logging
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from water_sample_ranker import PREVIEW_NOTICE, __commit__, __version__
from water_sample_ranker.config import AppConfig, CriteriaConfig, load_config, save_config
from water_sample_ranker.criteria.rules import NAMES, WEIGHTS
from water_sample_ranker.progress import ProgressCallback, ProgressUpdate
from water_sample_ranker.service import (
    ServiceError,
    ServiceResult,
    run_update,
    run_update_all,
    run_validate,
)

LOGGER = logging.getLogger(__name__)
Action = Callable[[AppConfig, ProgressCallback], ServiceResult]
FILE_FIELDS = (
    ("calculator", "Калькулятор"),
    ("fund", "Фонд скважин"),
    ("gdi", "Сводная таблица ГДИ"),
    ("lha", "База ЛХА"),
    ("daily", "Суточный рапорт"),
    ("well_history", "База по скважинам"),
    ("monitoring", "Мониторинг ГСС"),
    ("tech_mode", "Техрежим"),
    ("manual_input", "Предыдущая книга (ручные отметки)"),
    ("output", "Результат"),
)


class CriteriaDialog(QDialog):
    """Known rules are locked; TOP-N can be edited without pretending to approve scoring."""

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Критерии отбора проб воды")
        self.setModal(True)
        self.top_n = int(config.extra.get("top_n", 5))
        self.criteria = config.criteria
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Веса из презентации согласованы и редактируются в Excel. Пороги можно изменить "
                "для сценария.\n"
                "Пользовательские пороги требуют согласования. "
                "Предварительная сумма без перенормировки. Ручные ГТМ и веса — в Excel."
            )
        )
        self.table = QTableWidget(18, 6, self)
        self.table.setHorizontalHeaderLabels(
            ["Критерий", "Вес", "Оператор", "Порог / правило", "Единицы", "Изменение"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        conditions = (
            ("полные месяцы / 12", "не более 1", "мес.; дни", "Предварительный режим"),
            (">; <; >; <; >", "2000; 8000; 2000; 22000; 7", "мг/дм³; дни", "Фиксировано"),
            ("<; >=; >", "2000; 2000; 7", "мг/дм³; дни", "Фиксировано"),
            ("Т первым или К+Т", "точное исключение", "генезис", "Согласовано"),
            ("содержит", "?", "генезис", "Фиксировано"),
            (">; >", "1000; 2000", "л/сут; мг/дм³", "Только входное условие"),
            ("PENDING", "PENDING", "мм", "Требует согласования"),
            ("Да / Нет / Не задано", "ручной ввод в Excel", "—", "Согласовано"),
        )
        for row, (name, weight, condition) in enumerate(
            zip(NAMES[:8], WEIGHTS[:8], conditions, strict=True)
        ):
            self._row(row, (name, str(weight), *condition))
        self._row(9, ("Сепаратор ПК1", "—", ">= / <", "6.912", "м³/сут", "Не критерий рейтинга"))
        self._row(
            10,
            (
                "ТОП-N",
                "—",
                "1…30",
                str(self.top_n),
                "скважин",
                "Только размер диаграммы; полный фонд сохраняется",
            ),
        )
        item = self.table.item(10, 3)
        assert item is not None
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.parameters = (
            ("cl_low", "№2/3/6: нижний Cl", "> / < / >=", "мг/дм³"),
            ("cl_high", "№2: верхний предыдущий Cl", "<", "мг/дм³"),
            ("cl_delta", "№2: рост Cl", ">", "мг/дм³"),
            ("mineralization_max", "№2: обе минерализации", "<", "мг/дм³"),
            ("interval_days", "№2/3: интервал", ">", "дни"),
            ("water_l_day", "№6: дебит воды", ">", "л/сут"),
            ("separator_m3_day", "Сепаратор ПК1", ">= / <", "м³/сут"),
        )
        for row, (key, title, operator, unit) in enumerate(self.parameters, 11):
            self._row(
                row,
                (
                    title,
                    "—",
                    operator,
                    str(getattr(config.criteria, key)),
                    unit,
                    "Изменение = сценарий, требует согласования",
                ),
            )
            value = self.table.item(row, 3)
            assert value is not None
            value.setFlags(value.flags() | Qt.ItemFlag.ItemIsEditable)
        for col, width in enumerate((280, 55, 110, 190, 110)):
            self.table.setColumnWidth(col, width)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        defaults = QPushButton("По умолчанию")
        defaults.clicked.connect(self._defaults)
        buttons.addWidget(defaults)
        buttons.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self.resize(1250, 740)

    def _row(self, row: int, values: tuple[str, ...]) -> None:
        for col, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, col, item)

    def _defaults(self) -> None:
        item = self.table.item(10, 3)
        assert item is not None
        item.setText("5")
        defaults = CriteriaConfig()
        for row, (key, *_rest) in enumerate(self.parameters, 11):
            value = self.table.item(row, 3)
            assert value is not None
            value.setText(str(getattr(defaults, key)))

    def _save(self) -> None:
        try:
            item = self.table.item(10, 3)
            assert item is not None
            value = int(item.text())
            if not 1 <= value <= 30:
                raise ValueError("Диапазон 1…30")
        except ValueError as error:
            QMessageBox.warning(self, "Проверьте значение", str(error))
            return
        try:
            values = {}
            for row, (key, *_rest) in enumerate(self.parameters, 11):
                parameter = self.table.item(row, 3)
                assert parameter is not None
                values[key] = float(parameter.text().replace(",", "."))
            self.criteria = CriteriaConfig(**values)
        except ValueError as error:
            QMessageBox.warning(self, "Проверьте пороги", str(error))
            return
        self.top_n = value
        self.accept()


class Worker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress_changed = Signal(int, str)

    def __init__(self, action: Action, config: AppConfig) -> None:
        super().__init__()
        self.action = action
        self.config = config

    @Slot()
    def run(self) -> None:
        try:
            result = self.action(self.config, self._progress)
        except Exception as error:
            LOGGER.exception("Ошибка фоновой операции")
            self.failed.emit(str(error))
        else:
            self.finished.emit(result)

    def _progress(self, value: ProgressUpdate) -> None:
        if QThread.currentThread().isInterruptionRequested() and value.percent < 100:
            raise ServiceError("OPERATION_CANCELLED: отменено на безопасной контрольной точке")
        self.progress_changed.emit(value.percent, value.stage)


class LoaderWindow(QMainWindow):
    def __init__(self, config: AppConfig | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Water Sample Ranker — " + PREVIEW_NOTICE)
        self.setMinimumSize(820, 560)
        self.config = config or load_config()
        self._validated = False
        self._busy = False
        self._thread: QThread | None = None
        self._worker: Worker | None = None
        self._operation = ""
        self._fields: dict[str, QLineEdit] = {}
        self._browse_buttons = []
        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage(f"Версия {__version__} | {PREVIEW_NOTICE}")
        about = self.menuBar().addAction("О программе")
        about.triggered.connect(
            lambda: QMessageBox.information(
                self,
                "О программе",
                f"Water Sample Ranker {__version__}\n{PREVIEW_NOTICE}\n"
                "Предварительный рейтинг. Native Excel и ручная Windows UX: NOT_RUN.\n"
                "Qt/PySide6 и Shiboken6: LGPLv3; Python и другие компоненты: "
                "см. THIRD_PARTY_NOTICES.txt и licenses/ рядом с EXE.",
            )
        )
        central = QWidget(self)
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid)
        for row, (key, label) in enumerate(FILE_FIELDS):
            if key in {"monitoring", "tech_mode", "daily", "gdi"} and not self.config.path(key):
                continue
            grid.addWidget(QLabel(label), row, 0)
            edit = QLineEdit(self.config.paths.get(key, ""))
            edit.setClearButtonEnabled(True)
            grid.addWidget(edit, row, 1)
            self._fields[key] = edit
            if key != "output":
                edit.textChanged.connect(self._invalidate)
            button = QPushButton("Обзор…")
            button.clicked.connect(lambda _checked=False, name=key: self._browse(name))
            self._browse_buttons.append(button)
            grid.addWidget(button, row, 2)
        actions = QHBoxLayout()
        self._validate_button = QPushButton("Проверить файлы")
        self._validate_button.clicked.connect(self._validate)
        self._field_combo = QComboBox()
        self._field_combo.setPlaceholderText("Месторождение…")
        self._field_combo.currentTextChanged.connect(self._state)
        self._criteria_button = QPushButton("Критерии…")
        self._criteria_button.clicked.connect(self._criteria)
        self._update_button = QPushButton("Обновить калькулятор")
        self._update_button.clicked.connect(self._update)
        for widget in (
            self._validate_button,
            self._field_combo,
            self._criteria_button,
            self._update_button,
        ):
            actions.addWidget(widget)
        outer.addLayout(actions)
        self._cancel_button = QPushButton("Отмена")
        self._cancel_button.clicked.connect(self._cancel)
        actions.addWidget(self._cancel_button)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        outer.addWidget(self._progress_bar)
        outer.addWidget(QLabel("Статус"))
        self._status_box = QPlainTextEdit()
        self._status_box.setReadOnly(True)
        self._status_box.setPlainText(
            "Проверьте файлы, затем выберите одно месторождение. "
            "Предварительный рейтинг / методика частично согласована."
        )
        outer.addWidget(self._status_box, 1)
        self._state()

    @Slot()
    def _invalidate(self) -> None:
        self._validated = False
        self._field_combo.clear()
        self._field_combo.setCurrentIndex(-1)
        self._state()

    @Slot()
    def _state(self) -> None:
        self._cancel_button.setEnabled(self._busy)
        self._field_combo.setEnabled(self._validated and not self._busy)
        self._update_button.setEnabled(
            self._validated and bool(self._field_combo.currentText()) and not self._busy
        )
        for widget in (
            self._validate_button,
            self._criteria_button,
            *self._browse_buttons,
            *self._fields.values(),
        ):
            widget.setEnabled(not self._busy)

    def _cancel(self) -> None:
        if self._thread is not None:
            self._thread.requestInterruption()
            self._status_box.setPlainText(
                "Отмена запрошена; ожидание безопасной контрольной точки…"
            )

    def _browse(self, key: str) -> None:
        initial = str(self.config.path(key) or self.config.root)
        func = QFileDialog.getSaveFileName if key == "output" else QFileDialog.getOpenFileName
        file_filter = "Excel (*.xlsx *.xlsm)"
        selected, _ = func(self, "Выберите файл", initial, file_filter)
        if selected:
            self._fields[key].setText(selected)

    def _snapshot(self) -> AppConfig:
        from copy import deepcopy

        result = deepcopy(self.config)
        result.paths.update({key: edit.text().strip() for key, edit in self._fields.items()})
        result.selected_field = self._field_combo.currentText()
        return result

    def _criteria(self) -> None:
        dialog = CriteriaDialog(self.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config.extra["top_n"] = dialog.top_n
            self.config.criteria = dialog.criteria
            self.config = self._snapshot()
            save_config(self.config)

    def _start(self, action: Action, operation: str) -> None:
        if self._busy:
            return
        try:
            config = self._snapshot()
            save_config(config)
        except (OSError, ValueError) as error:
            self._status_box.setPlainText(str(error))
            return
        self.config = config
        self._busy = True
        self._operation = operation
        self._progress_bar.setValue(0)
        self._state()
        thread = QThread(self)
        worker = Worker(action, config)
        self._thread = thread
        self._worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress_changed.connect(self._progress)
        worker.finished.connect(self._success)
        worker.failed.connect(self._error)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(self._done)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    @Slot(int, str)
    def _progress(self, percent: int, text: str) -> None:
        self._progress_bar.setValue(percent)
        self._status_box.setPlainText(text)

    @Slot(object)
    def _success(self, result: ServiceResult) -> None:
        self._status_box.setPlainText(result.summary())
        if self._operation == "validate":
            self._validated = not result.has_errors
            if self._validated:
                self._field_combo.addItems(result.report["available_fields"])
                self._field_combo.addItem("Все месторождения")
                self._field_combo.setCurrentIndex(-1)
        if not result.has_errors:
            self._progress_bar.setValue(100)

    @Slot(str)
    def _error(self, message: str) -> None:
        self._status_box.setPlainText(message)
        if self._operation == "validate":
            self._invalidate()

    @Slot()
    def _done(self) -> None:
        # QThread.finished precedes final deferred QObject deletion. Keep Python
        # wrappers alive until the OS thread has completed its teardown.
        if self._thread is not None:
            self._thread.wait()
        self._thread = None
        self._worker = None
        self._busy = False
        self._state()

    def _validate(self) -> None:
        self._invalidate()
        self._start(lambda config, progress: run_validate(config, progress=progress), "validate")

    def _update(self) -> None:
        if not self._validated or not self._field_combo.currentText():
            return
        field = self._field_combo.currentText()
        if field == "Все месторождения":
            from datetime import datetime
            from uuid import uuid4

            def batch(config: AppConfig, progress: ProgressCallback) -> ServiceResult:
                directory = (
                    config.root
                    / "outputs"
                    / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
                )
                results = run_update_all(config, output_dir=directory, progress=progress)
                report = results[0].report.copy()
                report["bundle_outputs"] = [str(r.output) for r in results]
                report.pop("selected_evidence_stage_b", None)
                report.pop("selected_field", None)
                report["rows_written"] = sum(len(r.rows) for r in results)
                return ServiceResult(
                    report, tuple(row for r in results for row in r.rows), directory
                )

            self._start(batch, "update-all")
            return
        self._start(
            lambda config, progress: run_update(config, target_field=field, progress=progress),
            "update",
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._busy:
            event.ignore()
        else:
            try:
                self.config = self._snapshot()
                save_config(self.config)
            except (OSError, ValueError) as error:
                LOGGER.error("Не удалось сохранить config.toml: %s", error)
            event.accept()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--version"]:
        if sys.stdout:
            print(__version__)
        return 0
    if "--smoke-test" in args:
        import argparse
        from importlib.metadata import version

        from water_sample_ranker.config import TEMPLATE, project_root
        from water_sample_ranker.excel.compatibility import inspect_template

        parser = argparse.ArgumentParser()
        parser.add_argument("--smoke-test", action="store_true")
        parser.add_argument("--expected-version")
        parser.add_argument("--expected-commit")
        parser.add_argument("--smoke-receipt", type=Path)
        smoke = parser.parse_args(args)
        if smoke.expected_version and smoke.expected_version != __version__:
            raise ServiceError("FROZEN_VERSION_MISMATCH")
        if smoke.expected_commit and smoke.expected_commit != __commit__:
            raise ServiceError("FROZEN_COMMIT_MISMATCH")
        package_version = version("water-sample-ranker")
        if package_version != __version__:
            raise ServiceError("FROZEN_PACKAGE_VERSION_MISMATCH")
        result = inspect_template(project_root() / TEMPLATE)
        if result["capacity"] != 1000:
            raise ServiceError("Frozen template missing")
        app = QApplication.instance() or QApplication([sys.argv[0]])
        with tempfile.TemporaryDirectory(prefix="wsr-smoke-") as directory:
            window = LoaderWindow(AppConfig(Path(directory)))
            app.processEvents()
            if f"Версия {__version__}" not in window.statusBar().currentMessage():
                raise ServiceError("FROZEN_GUI_VERSION_MISMATCH")
            window.close()
        if smoke.smoke_receipt:
            with smoke.smoke_receipt.open("x", encoding="utf-8") as stream:
                json.dump(
                    {
                        "status": "PASS",
                        "version": __version__,
                        "package_version": package_version,
                        "source_commit": __commit__,
                        "gui_version": __version__,
                        "template_capacity": result["capacity"],
                        "human_ux": "NOT_RUN",
                    },
                    stream,
                )
        if sys.stdout is not None:
            print(f"Water Sample Ranker {__version__}: imports + template OK")
        return 0
    app = QApplication([sys.argv[0], *args])
    try:
        window = LoaderWindow()
    except (OSError, ValueError) as error:
        QMessageBox.critical(None, "Конфигурация", str(error))
        return 2
    window.show()
    return app.exec()
