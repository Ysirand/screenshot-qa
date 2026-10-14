"""收藏夹窗口:以截图为主角的卡片式浏览 + 右侧编辑栏。

设计要点:
- 横屏截图是常态,所以每张题图都按"宽度撑满卡片"来显示(最多 150px 高);
  绝不再塞进竖长框里 —— 那是上一版最难看的点。
- 题干直接就是这张图,文字摘要只作为搜索/归档用的辅助信息。
- 三栏:分类 | 题目卡片(可滚动) | 编辑栏。
"""

from __future__ import annotations


from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import plat
from .collection import DEFAULT_FOLDER, FOLDER_ALL, Collection
from .logging_setup import log
from .toast import toast

_STYLE = """
QDialog { background: #F4F5F7; }
QLabel { color: #2A2F3A; font-size: 13px; }
QLabel#hint { color: #6B7280; font-size: 12px; }
QLabel#section { color: #6B7280; font-size: 12px; }
QLineEdit, QPlainTextEdit {
    background: #FFFFFF; border: 1px solid #D8DCE3; border-radius: 8px;
    padding: 6px 10px; color: #1F2430; selection-background-color: #4C9BFF;
    selection-color: #FFFFFF;
}
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #4C9BFF; }
QComboBox {
    background: #FFFFFF; border: 1px solid #D8DCE3; border-radius: 8px;
    padding: 5px 10px; color: #1F2430;
}
QComboBox:focus { border: 1px solid #4C9BFF; }
QComboBox QAbstractItemView {
    background: #FFFFFF; color: #1F2430; border: 1px solid #D8DCE3;
    selection-background-color: #E4EFFF; selection-color: #1F2430;
}
QPushButton {
    background: #FFFFFF; border: 1px solid #D8DCE3; border-radius: 8px;
    padding: 6px 14px; color: #2A2F3A;
}
QPushButton:hover { background: #EEF4FF; border-color: #4C9BFF; color: #1656B8; }
QPushButton:pressed { background: #DCE9FF; }
QPushButton:disabled { color: #A8B0BC; background: #F0F1F3; }
QListWidget {
    background: #FFFFFF; border: 1px solid #E3E6EB; border-radius: 10px;
    padding: 4px; color: #2A2F3A;
}
QListWidget::item { padding: 7px 8px; border-radius: 6px; }
QListWidget::item:hover { background: #F0F4FA; }
QListWidget::item:selected { background: #E4EFFF; color: #1656B8; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #C9CFD8; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #AEB6C2; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

_ALL_LABEL = "全部题目"
_UNSORTED_LABEL = "未分类"
_IMAGE_HEIGHT = 150      # 卡片里截图的高度(宽度撑满)
_CARD_MIN_WIDTH = 320
_MAX_CARDS = 300         # 一次最多渲染这么多张,避免几百个控件卡顿


class AspectImage(QLabel):
    """按比例缩放显示图片,永不拉伸变形;点一下可以调外部程序打开原图。"""

    clicked = Signal()

    def __init__(self, height: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(height)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "QLabel { background: #EFF1F4; border: none;"
            " border-radius: 8px; color: #8A93A5; font-size: 12px; }"
        )
        self._source: QPixmap | None = None

    def set_image(self, pixmap: QPixmap | None, placeholder: str = "(没有截图)") -> None:
        self._source = pixmap
        if pixmap is None or pixmap.isNull():
            self.clear()
            self.setText(placeholder)
            self.setToolTip("")
            return
        self.setText("")
        self.setToolTip("点一下看原图")
        self._rescale()

    def _rescale(self) -> None:
        if self._source is None or self._source.isNull():
            return
        size = self.size()
        if size.width() < 20 or size.height() < 20:
            return
        self.setPixmap(self._source.scaled(
            size, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def resizeEvent(self, event):  # noqa: N802
        self._rescale()
        super().resizeEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class QuestionCard(QFrame):
    """一张题目卡片:上面是截图(主角),下面一行信息。"""

    clicked = Signal(int)

    def __init__(self, item_id: int, pixmap: QPixmap | None, title: str,
                 meta: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item_id = item_id
        self._selected = False
        self.setObjectName("card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(_CARD_MIN_WIDTH)

        box = QVBoxLayout(self)
        box.setContentsMargins(10, 10, 10, 8)
        box.setSpacing(6)

        self.image = AspectImage(_IMAGE_HEIGHT, self)
        self.image.set_image(pixmap, "(这道题没有截图)")
        self.image.clicked.connect(lambda: self.clicked.emit(self.item_id))
        box.addWidget(self.image)

        self.title = QLabel(title, self)
        self.title.setWordWrap(False)
        self.title.setStyleSheet("color: #1F2430; font-size: 13px; font-weight: 600;")
        box.addWidget(self.title)

        self.meta = QLabel(meta, self)
        self.meta.setStyleSheet("color: #6B7280; font-size: 11px;")
        box.addWidget(self.meta)

        self._apply_style()

    def set_selected(self, flag: bool) -> None:
        if self._selected == flag:
            return
        self._selected = flag
        self._apply_style()

    def _apply_style(self) -> None:
        if self._selected:
            self.setStyleSheet(
                "#card { background: #E4EFFF; border: 2px solid #4C9BFF;"
                " border-radius: 12px; }"
            )
        else:
            self.setStyleSheet(
                "#card { background: #FFFFFF; border: 1px solid #E3E6EB;"
                " border-radius: 12px; }"
                "#card:hover { background: #FAFBFD; border: 1px solid #C9D6EA; }"
            )

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.item_id)
        super().mousePressEvent(event)


class CollectionWindow(QDialog):
    """非模态窗口,开着它也能继续截图答题。"""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.collection = Collection()
        self._current_folder: str | None = FOLDER_ALL
        self._current_item: int | None = None
        self._cards: dict[int, QuestionCard] = {}
        self._thumb_cache: dict[int, QPixmap | None] = {}
        self._loading = False

        self.setWindowTitle("收藏夹 - 截图自动搜题")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setStyleSheet(_STYLE)
        self.setMinimumSize(1000, 620)
        self.resize(1240, 780)
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
        top_row.setSpacing(8)
        self.search = QLineEdit(top)
        self.search.setPlaceholderText("搜索题干 / 答案 / 批注…(本地即时搜索)")
        self.search.textChanged.connect(self.reload_cards)
        self.new_folder_btn = QPushButton("新建分类", top)
        self.new_folder_btn.clicked.connect(self.create_folder)
        self.stats = QLabel("", top)
        self.stats.setObjectName("hint")
        self.stats.setStyleSheet("color: #6B7280; font-size: 12px;")
        top_row.addWidget(self.search, 1)
        top_row.addWidget(self.new_folder_btn)
        top_row.addWidget(self.stats)
        root.addWidget(top)

        body = QWidget(self)
        body_row = QHBoxLayout(body)
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(10)

        # 左:分类
        left = QWidget(body)
        left.setFixedWidth(180)
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.setSpacing(6)
        folder_title = QLabel("分类", left)
        folder_title.setObjectName("section")
        left_box.addWidget(folder_title)
        self.folder_list = QListWidget(left)
        self.folder_list.currentRowChanged.connect(self._on_folder_changed)
        left_box.addWidget(self.folder_list, 1)
        folder_btns = QWidget(left)
        frow = QHBoxLayout(folder_btns)
        frow.setContentsMargins(0, 0, 0, 0)
        frow.setSpacing(6)
        rename_btn = QPushButton("重命名", folder_btns)
        rename_btn.clicked.connect(self.rename_folder)
        del_btn = QPushButton("删除", folder_btns)
        del_btn.setToolTip("只删分类,里面的题目会退回「未分类」")
        del_btn.clicked.connect(self.delete_folder)
        frow.addWidget(rename_btn)
        frow.addWidget(del_btn)
        left_box.addWidget(folder_btns)
        body_row.addWidget(left)

        # 中:题目卡片
        self.scroll = QScrollArea(body)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.card_host = QWidget()
        self.card_box = QVBoxLayout(self.card_host)
        self.card_box.setContentsMargins(0, 0, 6, 0)
        self.card_box.setSpacing(10)
        self.card_box.addStretch(1)
        self.scroll.setWidget(self.card_host)
        body_row.addWidget(self.scroll, 3)

        # 右:编辑栏
        right = QWidget(body)
        right.setFixedWidth(390)
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.setSpacing(6)

        self.empty_hint = QLabel("← 左边点一张题目卡片\n就可以在这里改题目、答案和批注", right)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setObjectName("hint")

        combo_row = QWidget(right)
        combo_layout = QHBoxLayout(combo_row)
        combo_layout.setContentsMargins(0, 0, 0, 0)
        combo_label = QLabel("分类", combo_row)
        combo_label.setObjectName("section")
        combo_layout.addWidget(combo_label)
        self.folder_combo = QComboBox(combo_row)
        self.folder_combo.setMinimumWidth(150)
        combo_layout.addWidget(self.folder_combo, 1)
        right_box.addWidget(combo_row)

        question_label = QLabel("题干摘要(给搜索用,可以自己改)")
        question_label.setObjectName("section")
        right_box.addWidget(question_label)
        self.question_edit = QPlainTextEdit(right)
        self.question_edit.setFixedHeight(56)
        self.question_edit.setPlaceholderText("题目文字摘要")
        right_box.addWidget(self.question_edit)

        answer_label = QLabel("答案")
        answer_label.setObjectName("section")
        right_box.addWidget(answer_label)
        self.answer_edit = QPlainTextEdit(right)
        self.answer_edit.setPlaceholderText("答案")
        right_box.addWidget(self.answer_edit, 1)

        note_label = QLabel("我的批注")
        note_label.setObjectName("section")
        right_box.addWidget(note_label)
        self.note_edit = QPlainTextEdit(right)
        self.note_edit.setFixedHeight(70)
        self.note_edit.setPlaceholderText("随手写点易错点、思路…(本地保存,即时)")
        right_box.addWidget(self.note_edit)

        btns = QWidget(right)
        brow = QHBoxLayout(btns)
        brow.setContentsMargins(0, 0, 0, 0)
        brow.setSpacing(6)
        self.save_btn = QPushButton("保存修改", btns)
        self.save_btn.clicked.connect(self.save_current)
        copy_btn = QPushButton("复制答案", btns)
        copy_btn.clicked.connect(self.copy_answer)
        image_btn = QPushButton("看原图", btns)
        image_btn.clicked.connect(self.open_image)
        del_item_btn = QPushButton("删除", btns)
        del_item_btn.clicked.connect(self.delete_current)
        brow.addWidget(self.save_btn)
        brow.addWidget(copy_btn)
        brow.addWidget(image_btn)
        brow.addWidget(del_item_btn)
        right_box.addWidget(btns)
        right_box.addWidget(self.empty_hint)

        body_row.addWidget(right)
        root.addWidget(body, 1)
        self._set_detail_enabled(False)

    def _set_detail_enabled(self, enabled: bool) -> None:
        for w in (self.question_edit, self.answer_edit, self.note_edit,
                  self.save_btn, self.folder_combo):
            w.setEnabled(enabled)
        self.empty_hint.setVisible(not enabled)

    # ---------- 刷新 ----------

    def reload(self) -> None:
        self.reload_folders()
        self.reload_cards()

    def reload_folders(self) -> None:
        counts = self.collection.counts()
        folders = self.collection.folders()

        self._loading = True
        self.folder_list.clear()
        self.folder_list.addItem(QListWidgetItem("%s (%d)" % (_ALL_LABEL, counts.get(FOLDER_ALL, 0))))
        self.folder_list.addItem(QListWidgetItem("%s (%d)" % (_UNSORTED_LABEL, counts.get(DEFAULT_FOLDER, 0))))
        for name in folders:
            self.folder_list.addItem(QListWidgetItem("%s (%d)" % (name, counts.get(name, 0))))
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
        self.stats.setText("共 %d 道 · %d 个分类" % (counts.get(FOLDER_ALL, 0), len(folders)))

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

    def _pixmap_for(self, item_id: int) -> QPixmap | None:
        if item_id in self._thumb_cache:
            return self._thumb_cache[item_id]
        path = self.collection.image_path(item_id)
        pixmap = None
        if path is not None:
            raw = QPixmap(str(path))
            if not raw.isNull():
                pixmap = raw
        self._thumb_cache[item_id] = pixmap
        return pixmap

    def reload_cards(self) -> None:
        self._loading = True
        # 清空旧卡片
        while self.card_box.count() > 1:
            item = self.card_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards.clear()

        rows = self.collection.items(folder=self._current_folder, keyword=self.search.text())
        shown = rows[:_MAX_CARDS]
        for row in shown:
            item_id = int(row["id"])
            title = (row["question"] or "").strip().splitlines()[0][:70] if row["question"] else "(没有题干)"
            note_flag = " · 有批注" if (row["note"] or "").strip() else ""
            meta = "%s · %s%s" % (row["created_at"][5:16], row["folder"] or _UNSORTED_LABEL, note_flag)
            card = QuestionCard(item_id, self._pixmap_for(item_id), title, meta, self.card_host)
            card.clicked.connect(self.select_item)
            self._cards[item_id] = card
            self.card_box.insertWidget(self.card_box.count() - 1, card)

        if len(rows) > len(shown):
            more = QLabel("只显示了前 %d 张,用搜索缩小范围" % _MAX_CARDS, self.card_host)
            more.setObjectName("hint")
            self.card_box.insertWidget(self.card_box.count() - 1, more)

        self._loading = False

        if shown:
            self.select_item(int(shown[0]["id"]))
        else:
            self._current_item = None
            self.question_edit.setPlainText("")
            self.answer_edit.setPlainText("")
            self.note_edit.setPlainText("")
            self._set_detail_enabled(False)

    def select_item(self, item_id: int) -> None:
        """选中一张卡片,右侧显示它的内容。"""
        record = self.collection.get_item(item_id)
        if record is None:
            return
        for card_id, card in self._cards.items():
            card.set_selected(card_id == item_id)
        self._current_item = item_id
        self._set_detail_enabled(True)
        self._loading = True
        self.question_edit.setPlainText(record["question"] or "")
        self.answer_edit.setPlainText(record["answer"] or "")
        self.note_edit.setPlainText(record["note"] or "")
        index = self.folder_combo.findData(record["folder"] or DEFAULT_FOLDER)
        self.folder_combo.setCurrentIndex(max(0, index))
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
        self.reload_cards()

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
        folder = self.folder_combo.currentData() or DEFAULT_FOLDER
        self.collection.update_item(
            self._current_item,
            question=self.question_edit.toPlainText(),
            answer=self.answer_edit.toPlainText(),
            note=self.note_edit.toPlainText(),
            folder=folder,
        )
        self.collection.ensure_folder(folder)
        self.reload()
        self.changed.emit()
        toast("已保存(" + (folder or _UNSORTED_LABEL) + ")", 1600)

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
        self._thumb_cache.pop(self._current_item, None)
        self.collection.delete_item(self._current_item)
        self._current_item = None
        self.reload()
        self.changed.emit()
        toast("已删除", 1500)

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
            plat.open_path(path)
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