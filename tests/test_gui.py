import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from water_sample_ranker.config import AppConfig
from water_sample_ranker.gui.app import CriteriaDialog, LoaderWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    assert isinstance(instance, QApplication)
    return instance


def wait_thread(app: QApplication, window: LoaderWindow) -> None:
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: loop.quit() if window._thread is None else None)
    timer.start()
    QTimer.singleShot(15000, loop.quit)
    loop.exec()
    assert window._thread is None
    app.processEvents()


def test_gui_validation_gate_thread_and_input_reset(
    app: QApplication, synthetic: AppConfig
) -> None:
    window = LoaderWindow(synthetic)
    assert window._field_combo.currentIndex() == -1
    assert not window._update_button.isEnabled()
    assert window._progress_bar.minimum() == 0 and window._progress_bar.maximum() == 100
    assert window._validate_button.text() == "Проверить файлы"
    assert window._criteria_button.text() == "Критерии…"
    assert window._update_button.text() == "Обновить калькулятор"
    window._validate()
    assert window._busy
    wait_thread(app, window)
    assert window._validated
    assert "FRESHNESS_POLICY_PENDING" in window._status_box.toPlainText()
    assert window._field_combo.count() == 4
    assert window._field_combo.itemText(3) == "Все месторождения"
    assert window._field_combo.currentIndex() == -1
    assert not window._update_button.isEnabled()
    window._field_combo.setCurrentIndex(0)
    assert window._update_button.isEnabled()
    window._fields["output"].setText("outputs/new.xlsx")
    assert window._update_button.isEnabled()
    window._fields["lha"].setText("missing.xlsx")
    assert not window._validated and not window._update_button.isEnabled()
    window._validate()
    wait_thread(app, window)
    assert not window._validated and window._progress_bar.value() < 100
    window.close()


def test_criteria_dialog_exposes_units_and_pending_method(
    app: QApplication, tmp_path: Path
) -> None:
    from PySide6.QtCore import Qt

    dialog = CriteriaDialog(AppConfig(tmp_path))
    assert dialog.isModal()
    pending = dialog.table.item(0, 3)
    assert pending is not None and pending.text() == "не более 1"
    assert not pending.flags() & Qt.ItemFlag.ItemIsEditable
    value = dialog.table.item(10, 3)
    assert value is not None and value.flags() & Qt.ItemFlag.ItemIsEditable
    value.setText("12")
    dialog._defaults()
    assert value.text() == "5"
    dialog._save()
    assert dialog.top_n == 5


def test_worker_failure_teardown_is_safe(app: QApplication, tmp_path: Path) -> None:
    from water_sample_ranker.service import ServiceError

    window = LoaderWindow(AppConfig(tmp_path))

    def fail(config, progress):
        raise ServiceError("synthetic worker failure")

    for _ in range(20):
        window._start(fail, "validate")
        wait_thread(app, window)
        assert not window._busy
        assert "synthetic worker failure" in window._status_box.toPlainText()
        assert window._progress_bar.value() < 100
    window.close()


def test_threshold_editor_returns_marked_custom_snapshot(app: QApplication, tmp_path: Path) -> None:
    dialog = CriteriaDialog(AppConfig(tmp_path))
    item = dialog.table.item(13, 3)  # Cl delta, not a weight or an unknown scoring function.
    assert item is not None
    item.setText("2500")
    dialog._save()
    assert dialog.criteria.cl_delta == 2500
    assert dialog.criteria.policy_status == "CUSTOM_THRESHOLDS_PENDING_APPROVAL"


def test_all_fields_optional_inputs_and_worker_cancel(app: QApplication, synthetic: AppConfig):
    from PySide6.QtCore import QThread

    from water_sample_ranker.progress import ProgressUpdate

    window = LoaderWindow(synthetic)
    assert "gtm_plan" not in window._fields and "geodata" not in window._fields
    assert "well_history" in window._fields and "manual_input" in window._fields
    assert not window._cancel_button.isEnabled()
    window._validate()
    wait_thread(app, window)
    window._field_combo.setCurrentText("Все месторождения")
    window._update()
    wait_thread(app, window)
    markers = list((synthetic.root / "outputs").rglob("bundle-complete.json"))
    assert len(markers) == 1
    assert len(list(markers[0].parent.glob("*.xlsx"))) == 3

    def slow(config, progress):
        for n in range(1, 10):
            QThread.msleep(20)
            progress(ProgressUpdate(n, "SYNTHETIC_CHECKPOINT"))

    window._start(slow, "validate")
    assert window._cancel_button.isEnabled()
    window._cancel()
    wait_thread(app, window)
    assert "OPERATION_CANCELLED" in window._status_box.toPlainText()
    assert not window._busy and not window._cancel_button.isEnabled()
    assert not window._validated
    window.close()
