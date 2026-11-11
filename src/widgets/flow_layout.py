"""A compact action row that wraps only when its controls no longer fit."""
from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout


class FlowLayout(QLayout):
    def __init__(self, parent=None, *, spacing=8):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), measure=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, measure=False)

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        return size + QSize(left + right, top + bottom)

    def sizeHint(self):
        visible = [item.sizeHint() for item in self._items if not item.isEmpty()]
        left, top, right, bottom = self.getContentsMargins()
        return QSize(sum(size.width() for size in visible) + max(0, len(visible) - 1) * self.spacing() + left + right,
                     max((size.height() for size in visible), default=0) + top + bottom)

    def _arrange(self, rect, *, measure):
        left, top, right, bottom = self.getContentsMargins()
        area = rect.adjusted(left, top, -right, -bottom)
        x, y, line_height = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            size = item.sizeHint()
            if line_height and x + size.width() > area.right() + 1:
                x = area.x()
                y += line_height + self.spacing()
                line_height = 0
            if not measure:
                item.setGeometry(QRect(x, y, size.width(), size.height()))
            x += size.width() + self.spacing()
            line_height = max(line_height, size.height())
        return y + line_height - rect.y() + bottom
