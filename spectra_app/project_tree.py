"""Width-aware Project rows, using Qt's native item rendering and navigation."""
from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QTextLayout, QTextOption
from PySide6.QtWidgets import QHeaderView, QStyledItemDelegate, QStyle, QStyleOptionViewItem, QTreeWidget


class _ProjectItemDelegate(QStyledItemDelegate):
    def _section_gap(self, index):
        if not index.parent().isValid() and index.row() > 0:
            return max(3, self.parent().fontMetrics().height() // 3)
        return 0

    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        option.rect.adjust(0, self._section_gap(index), 0, 0)
        super().paint(painter, option, index)

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        tree = self.parent()
        style = tree.style()
        text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, option, tree)
        margin = style.pixelMetric(QStyle.PM_FocusFrameHMargin, option, tree) + 1
        width = max(1, text_rect.width() - 2 * margin)
        layout = QTextLayout(option.text, option.font)
        text_option = QTextOption()
        text_option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        text_option.setTextDirection(option.direction)
        layout.setTextOption(text_option)
        # Qt's native item style uses WordWrap, which clips unbroken filenames.
        # Supply display-only line breaks; model text and accessible names stay intact.
        # QTextLine offsets count UTF-16 units, including supplementary characters.
        encoded = option.text.encode('utf-16-le')
        lines = []
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            start = line.textStart() * 2
            end = start + line.textLength() * 2
            lines.append(encoded[start:end].decode('utf-16-le').rstrip('\u2028'))
        layout.endLayout()
        option.text = '\u2028'.join(lines)
        # Only these measured breaks should wrap; a second WordWrap pass can
        # turn a trailing space at the boundary into an extra empty line.
        option.features &= ~QStyleOptionViewItem.WrapText

    def sizeHint(self, option, index):
        tree = self.parent()
        depth = int(tree.rootIsDecorated())
        ancestor = index.parent()
        while ancestor.isValid():
            depth += 1
            ancestor = ancestor.parent()
        option = QStyleOptionViewItem(option)
        # QTreeView supplies an invalid size-hint rectangle. Give the native
        # delegate the same usable column width that paint() receives.
        width = max(1, tree.viewport().width() - depth * tree.indentation())
        option.rect = QRect(0, 0, width, 1)
        option.features |= QStyleOptionViewItem.WrapText
        size = super().sizeHint(option, index)
        # A small native-font gap separates source parents without adding cards.
        size.setHeight(size.height() + self._section_gap(index))
        return size


class ProjectTreeWidget(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideNone)
        self.setUniformRowHeights(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.header().setSectionResizeMode(QHeaderView.Stretch)
        self.setItemDelegate(_ProjectItemDelegate(self))
        self._filter_text = ''
        self._filter_expanded = None

    def filter_items(self, text):
        """Hide unmatched branches without replacing rows or their identities."""
        query = text.strip().casefold()
        if query and not self._filter_text:
            self._filter_expanded = {}
            def remember(parent):
                for index in range(parent.childCount()):
                    item = parent.child(index)
                    self._filter_expanded[item.data(0, Qt.UserRole)] = item.isExpanded()
                    remember(item)
            remember(self.invisibleRootItem())
        self._filter_text = query

        def visit(parent, ancestor_matches=False):
            visible = False
            for index in range(parent.childCount()):
                item = parent.child(index)
                value = item.data(0, Qt.UserRole + 2) or item.toolTip(0)
                matches = not query or query in (item.text(0) + '\n' + str(value)).casefold()
                children = visit(item, ancestor_matches or matches)
                show = matches or ancestor_matches or children
                item.setHidden(not show)
                if query and children:
                    item.setExpanded(True)
                elif not query and self._filter_expanded is not None:
                    item.setExpanded(self._filter_expanded.get(item.data(0, Qt.UserRole), True))
                visible |= show
            return visible
        visit(self.invisibleRootItem())
        if not query:
            self._filter_expanded = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # This also runs when a vertical scrollbar changes the viewport width.
        self.scheduleDelayedItemsLayout()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange, QEvent.LayoutDirectionChange):
            self.scheduleDelayedItemsLayout()
