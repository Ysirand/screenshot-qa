"""收藏夹窗口:分类 / 批注 / 搜索。

性能取舍:分类、批注、搜索**全部走本地 SQLite**,点一下就是毫秒级,不经过大模型;
大模型只负责"答题"这一件事。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .collection import DEFAULT_FOLDER, FOLDER_ALL, Collection
from .logging_setup import log
from .toast import toast

_ALL_LABEL = "全部题目"
_UNSORTED_LABEL = "未分类"


class CollectionWindow(QDialog):
    """非模态窗口,开着它也能继续截图答题。"""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.collection = Collection()
        self._current_folder: str | None = FOLDER_ALL
        self._current_item: int | None = None
        self._loading = False

        self.setWindowTitle("收藏夹 - 截图自动搜题")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumSize(880, 560)
        self.resize(980, 640)
        self._build_ui()
        self.reload()

    # ---------- 界面 ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        top = QWidget(self)
        top_row = QHBoxLayout(top)
        top_row.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit(top)
        self.search.setPlaceholderText("搜索题干 / 答案 / 批注…(本地即时搜索)")
        self.search.textChanged.connect(self.reload_items)
        self.new_folder_btn = QPushButton("新建分类", top)
        self.new_folder_btn.clicked.connect(self.create_folder)
        self.stats = QLabel("", top)
        top_row.addWidget(self.search, 1)
        top_row.addWidget(self.new_folder_btn)
        top_row.addWidget(self.stats)
        root.addWidget(top)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # 左:分类
        left = QWidget(splitter)
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.setSpacing(6)
        left_box.addWidget(QLabel("分类", left))
        self.folder_list = QListWidget(left)
        self.folder_list.currentRowChanged.connect(self._on_folder_changed)
        left_box.addWidget(self.folder_list, 1)
        folder_btns = QWidget(left)
        folder_row = QHBoxLayout(folder_btns)
        folder_row.setContentsMargins(0, 0, 0, 0)
        rename_btn = QPushButton("重命名", folder_btns)
        rename_btn.clicked.connect(self.rename_folder)
        del_btn = QPushButton("删除分类", folder_btns)
        del_btn.setToolTip("只删分类,里面的题目会退回「未分类」")
        del_btn.clicked.connect(self.delete_folder)
        folder_row.addWidget(rename_btn)
        folder_row.addWidget(del_btn)
        left_box.addWidget(folder_btns)
        splitter.addWidget(left)

        # 右:列表 + 详情
        right = QWidget(splitter)
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.setSpacing(6)

        self.item_list = QListWidget(right)
        self.item_list.currentRowChanged.connect(self._on_item_changed)
        right_box.addWidget(self.item_list, 3)

        detail = QWidget(right)
        detail_box = QVBoxLayout(detail)
        detail_box.setContentsMargins(0, 0, 0, 0)
        detail_box.setSpacing(6)

        row1 = QWidget(detail)
        row1_box = QHBoxLayout(row1)
        row1_box.setContentsMargins(0, 0, 0, 0)
        row1_box.addWidget(QLabel("题干", row1))
        self.folder_combo = QComboBox(row1)
        self.folder_combo.setMinimumWidth(140)
        row1_box.addStretch(1)
        row1_box.addWidget(QLabel("分类", row1))
        row1_box.addWidget(self.folder_combo)
        detail_box.addWidget(row1)

        self.question_edit = QPlainTextEdit(detail)
        self.question_edit.setPlaceholderText("题干(可编辑,方便改错字)")
        self.question_edit.setFixedHeight(70)
        detail_box.addWidget(self.question_edit)

        detail_box.addWidget(QLabel("答案"))
        self.answer_edit = QPlainTextEdit(detail)
        self.answer_edit.setPlaceholderText("答案")
        detail_box.addWidget(self.answer_edit, 1)

        detail_box.addWidget(QLabel("我的批注"))
        self.note_edit = QPlainTextEdit(detail)
        self.note_edit.setFixedHeight(64)
        self.note_edit.setPlaceholderText("随手写点易错点、思路…(本地保存,即时)")
        detail_box.addWidget(self.note_edit)

        btns = QWidget(detail)
        btn_row = QHBoxLayout(btns)
        btn_row.setContentsMargins(0, 0, 0, 0)
        self.save_btn = QPushButton("保存修改", btns)
        self.save_btn.clicked.connect(self.save_current)
        copy_btn = QPushButton("复制答案", btns)
        copy_btn.clicked.connect(self.copy_answer)
        image_btn = QPushButton("看原图", btns)
        image_btn.clicked.connect(self.open_image)
        del_item_btn = QPushButton("删除这道题", btns)
        del_item_btn.clicked.connect(self.delete_current)
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(copy_btn)
        btn_row.addWidget(image_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(del_item_btn)
        detail_box.addWidget(btns)

        right_box.addWidget(detail, 4)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 760])
        root.addWidget(splitter, 1)

    # ---------- 数据刷新 ----------

    def reload(self) -> None:
        """重新读分类和列表(全本地,很快)。"""
        self.reload_folders()
        self.reload_items()

    def reload_folders(self) -> None:
        counts = self.collection.counts()
        folders = self.collection.folders()

        self._loading = True
        self.folder_list.clear()
        self.folder_list.addItem(QListWidgetItem("%s (%d)" % (_ALL_LABEL, counts.get(FOLDER_ALL, 0))))
        self.folder_list.addItem(QListWidgetItem("%s (%d)" % (_UNSORTED_LABEL, counts.get(DEFAULT_FOLDER, 0))))
        for name in folders:
            self.folder_list.addItem(QListWidgetItem("%s (%d)" % (name, counts.get(name, 0))))
        # 当前选中的分类在列表里的位置
        row = 0
        if self._current_folder == DEFAULT_FOLDER:
            row = 1
        elif self._current_folder not in (None, FOLDER_ALL):
            try:
                row = 2 + folders.index(self._current_folder)
            except ValueError:
                row = 0
        self.folder_list.setCurrentRow(row)
        self._loading = False

        self.stats.setText("共 %d 道,分类 %d 个" % (counts.get(FOLDER_ALL, 0), len(folders)))

        current = self.folder_combo.currentData()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.addItem(_UNSORTED_LABEL, DEFAULT_FOLDER)
        for name in folders:
            self.folder_combo.addItem(name, name)
        if current:
            index = self.folder_combo.findData(current)
            if index >= 0:
                self.folder_combo.setCurrentIndex(index)
        self.folder_combo.blockSignals(False)

    def reload_items(self) -> None:
        self._loading = True
        self.item_list.clear()
        rows = self.collection.items(folder=self._current_folder, keyword=self.search.text())
        for row in rows:
            title = (row["question"] or "(没有题干)").strip().splitlines()[0][:60] if row["question"] else "(没有题干)"
            folder = row["folder"] or _UNSORTED_LABEL
            text = "%s  ·  %s   [%s]" % (row["created_at"][5:16], title, folder)
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            self.item_list.addItem(item)
        self._loading = False
        self._current_item = None
        self._clear_detail()
        if rows:
            self.item_list.setCurrentRow(0)

    def _clear_detail(self) -> None:
        self._loading = True
        self.question_edit.setPlainText("")
        self.answer_edit.setPlainText("")
        self.note_edit.setPlainText("")
        self._loading = False

    # ---------- 事件 ----------

    def _on_folder_changed(self, row: int) -> None:
        if self._loading or row < 0:
            return
        if row == 0:
            self._current_folder = FOLDER_ALL
        elif row == 1:
            self._current_folder = DEFAULT_FOLDER
        else:
            folders = self.collection.folders()
            index = row - 2
            self._current_folder = folders[index] if 0 <= index < len(folders) else FOLDER_ALL
        self.reload_items()

    def _on_item_changed(self, row: int) -> None:
        if self._loading or row < 0:
            return
        item = self.item_list.item(row)
        if item is None:
            return
        item_id = int(item.data(Qt.ItemDataRole.UserRole))
        record = self.collection.get_item(item_id)
        if record is None:
            return
        self._current_item = item_id
        self._loading = True
        self.question_edit.setPlainText(record["question"] or "")
        self.answer_edit.setPlainText(record["answer"] or "")
        self.note_edit.setPlainText(record["note"] or "")
        index = self.folder_combo.findData(record["folder"] or DEFAULT_FOLDER)
        self.folder_combo.setCurrentIndex(max(0, index))
        self._loading = False

    # ---------- 操作 ----------

    def create_folder(self) -> None:
        name, ok = QInputDialog.getText(self, "新建分类", "分类名字:")
        if not ok or not name.strip():
            return
        if not self.collection.add_folder(name.strip()):
            QMessageBox.warning(self, "没建成", "分类名不能为空。")
            return
        self._current_folder = name.strip()
        self.reload()
        toast("已新建分类:" + name.strip(), 1600)

    def rename_folder(self) -> None:
        if self._current_folder in (None, FOLDER_ALL, DEFAULT_FOLDER):
            QMessageBox.information(self, "提示", "请先选中一个自己建的分类。")
            return
        old = self._current_folder
        name, ok = QInputDialog.getText(self, "重命名分类", "新名字:", text=old)
        if not ok or not name.strip() or name.strip() == old:
            return
        self.collection.rename_folder(old, name.strip())
        self._current_folder = name.strip()
        self.reload()
        toast("分类已重命名为:" + name.strip(), 1600)

    def delete_folder(self) -> None:
        if self._current_folder in (None, FOLDER_ALL, DEFAULT_FOLDER):
            QMessageBox.information(self, "提示", "「全部题目」和「未分类」不能删除。")
            return
        name = self._current_folder
        answer = QMessageBox.question(
            self, "删除分类",
            "删除分类「%s」?\n\n里面的题目不会被删,会退回到「未分类」。" % name,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.collection.delete_folder(name)
        self._current_folder = FOLDER_ALL
        self.reload()
        toast("分类已删除,题目已退回未分类", 1800)

    def save_current(self) -> None:
        if self._current_item is None:
            return
        self.collection.update_item(
            self._current_item,
            question=self.question_edit.toPlainText(),
            answer=self.answer_edit.toPlainText(),
            note=self.note_edit.toPlainText(),
            folder=self.folder_combo.currentData() or DEFAULT_FOLDER,
        )
        folder = self.folder_combo.currentData() or DEFAULT_FOLDER
        self.collection.ensure_folder(folder)
        folder_name = folder or _UNSORTED_LABEL
        self._current_folder = None if self._current_folder == FOLDER_ALL else self._current_folder
        self.reload()
        toast("已保存(" + folder_name + ")", 1600)
        self.changed.emit()

    def delete_current(self) -> None:
        if self._current_item is None:
            return
        answer = QMessageBox.question(
            self, "删除", "确定删除这道题?(截图也会一起删掉)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.collection.delete_item(self._current_item)
        self.reload()
        toast("已删除", 1500)
        self.changed.emit()

    def copy_answer(self) -> None:
        text = self.answer_edit.toPlainText().strip()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        toast("答案已复制", 1500)

    def open_image(self) -> None:
        if self._current_item is None:
            return
        path = self.collection.image_path(self._current_item)
        if path is None:
            toast("这道题没有截图", 1500)
            return
        try:
            os.startfile(str(path))  # noqa: S606
        except OSError as exc:
            log.warning("打开图片失败: %s", exc)
            toast("打开失败:" + str(exc), 2000)


_global_window: CollectionWindow | None = None


def show_collection_window(parent: QWidget | None = None) -> CollectionWindow:
    """打开(或前置)收藏夹窗口。"""
    global _global_window
    if _global_window is None:
        _global_window = CollectionWindow(parent)
    _global_window.reload()
    _global_window.show()
    _global_window.raise_()
    _global_window.activateWindow()
    return _global_window