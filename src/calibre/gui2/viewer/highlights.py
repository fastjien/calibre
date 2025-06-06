#!/usr/bin/env python
# License: GPL v3 Copyright: 2020, Kovid Goyal <kovid at kovidgoyal.net>

import json
import math
from collections import defaultdict
from functools import lru_cache
from itertools import chain

from qt.core import (
    QAbstractItemView,
    QColor,
    QDialog,
    QDialogButtonBox,
    QFont,
    QHBoxLayout,
    QIcon,
    QImage,
    QItemSelectionModel,
    QKeySequence,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
    QPushButton,
    QRect,
    QSize,
    QSizePolicy,
    QStyle,
    Qt,
    QTextCursor,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)

from calibre.constants import builtin_colors_dark, builtin_colors_light, builtin_decorations
from calibre.ebooks.epub.cfi.parse import cfi_sort_key
from calibre.gui2 import error_dialog, is_dark_theme, safe_open_url
from calibre.gui2.dialogs.confirm_delete import confirm
from calibre.gui2.gestures import GestureManager
from calibre.gui2.library.annotations import ChapterGroup, Details, render_notes
from calibre.gui2.library.annotations import Export as ExportBase
from calibre.gui2.viewer import get_boss, link_prefix_for_location_links
from calibre.gui2.viewer.config import vprefs
from calibre.gui2.viewer.search import SearchInput
from calibre.gui2.viewer.shortcuts import get_shortcut_for, index_to_key_sequence
from calibre.gui2.widgets2 import Dialog
from calibre.utils.localization import _, ngettext
from calibre_extensions.progress_indicator import set_no_activate_on_click

decoration_cache = {}
highlight_role = Qt.ItemDataRole.UserRole
section_role = highlight_role + 1


@lru_cache(maxsize=8)
def wavy_path(width, height, y_origin):
    half_height = height / 2
    path = QPainterPath()
    pi2 = math.pi * 2
    num = 100
    num_waves = 4
    wav_limit = num // num_waves
    sin = math.sin
    path.reserve(num)
    for i in range(num):
        x = width * i / num
        rads = pi2 * (i % wav_limit) / wav_limit
        factor = sin(rads)
        y = y_origin + factor * half_height
        path.lineTo(x, y) if i else path.moveTo(x, y)
    return path


def compute_style_key(style):
    return tuple((k, style[k]) for k in sorted(style))


def decoration_for_style(palette, style, icon_size, device_pixel_ratio, is_dark):
    style_key = (is_dark, icon_size, device_pixel_ratio, compute_style_key(style))
    sentinel = object()
    ans = decoration_cache.get(style_key, sentinel)
    if ans is not sentinel:
        return ans
    ans = None
    kind = style.get('kind')
    if kind == 'color':
        key = 'dark' if is_dark else 'light'
        val = style.get(key)
        if val is None:
            which = style.get('which')
            val = (builtin_colors_dark if is_dark else builtin_colors_light).get(which)
        if val is None:
            val = style.get('background-color')
        if val is not None:
            ans = QColor(val)
    elif kind == 'decoration':
        which = style.get('which')
        if which is not None:
            q = builtin_decorations.get(which)
            if q is not None:
                style = q
        sz = math.ceil(icon_size * device_pixel_ratio)
        canvas = QImage(sz, sz, QImage.Format.Format_ARGB32)
        canvas.fill(Qt.GlobalColor.transparent)
        canvas.setDevicePixelRatio(device_pixel_ratio)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(palette.color(QPalette.ColorRole.WindowText))
        irect = QRect(0, 0, icon_size, icon_size)
        adjust = -2
        text_rect = p.drawText(irect.adjusted(0, adjust, 0, adjust), Qt.AlignmentFlag.AlignHCenter| Qt.AlignmentFlag.AlignTop, 'a')
        p.drawRect(irect)
        fm = p.fontMetrics()
        pen = p.pen()
        if 'text-decoration-color' in style:
            pen.setColor(QColor(style['text-decoration-color']))
        lstyle = style.get('text-decoration-style') or 'solid'
        q = {'dotted': Qt.PenStyle.DotLine, 'dashed': Qt.PenStyle.DashLine, }.get(lstyle)
        if q is not None:
            pen.setStyle(q)
        lw = fm.lineWidth()
        if lstyle == 'double':
            lw * 2
        pen.setWidth(fm.lineWidth())
        q = style.get('text-decoration-line') or 'underline'
        pos = text_rect.bottom()
        height = irect.bottom() - pos
        if q == 'overline':
            pos = height
        elif q == 'line-through':
            pos = text_rect.center().y() - adjust - lw // 2
        p.setPen(pen)
        if lstyle == 'wavy':
            p.drawPath(wavy_path(icon_size, height, pos))
        else:
            p.drawLine(0, pos, irect.right(), pos)
        p.end()
        ans = QPixmap.fromImage(canvas)
    elif 'background-color' in style:
        ans = QColor(style['background-color'])
    decoration_cache[style_key] = ans
    return ans


class SwatchList(QListWidget):

    def __init__(self, all_styles, selected_styles, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setViewMode(QListView.ViewMode.IconMode)
        icon_size = parent.style().pixelMetric(QStyle.PixelMetric.PM_IconViewIconSize, None, self)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setSpacing(20)
        dpr = self.devicePixelRatioF()
        is_dark = is_dark_theme()
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemNeverHasChildren
        self.itemClicked.connect(self.toggle_item)
        for s in all_styles:
            key = compute_style_key(s)
            dec = decoration_for_style(self.palette(), s, icon_size, dpr, is_dark)
            i = QListWidgetItem(self)
            i.setFlags(flags)
            i.setData(Qt.ItemDataRole.UserRole, s)
            i.setData(Qt.ItemDataRole.UserRole + 1, key)
            if dec:
                i.setData(Qt.ItemDataRole.DecorationRole, dec)
            i.setCheckState(Qt.CheckState.Checked if selected_styles and key in selected_styles else Qt.CheckState.Unchecked)

    def toggle_item(self, item):
        item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked)

    @property
    def selected_styles(self):
        for i in range(self.count()):
            item = self.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                yield item.data(Qt.ItemDataRole.UserRole + 1)

    def select_all(self):
        for i in range(self.count()):
            self.item(i).setCheckState(Qt.CheckState.Checked)

    def select_none(self):
        for i in range(self.count()):
            self.item(i).setCheckState(Qt.CheckState.Unchecked)


class FilterDialog(Dialog):

    def __init__(self, all_styles, show_only_styles, parent=None):
        self.all_styles, self.show_only_styles = all_styles, show_only_styles
        super().__init__(_('Filter shown highlights'), 'filter-highlights', parent=parent)

    def sizeHint(self):
        return QSize(500, 400)

    def setup_ui(self):
        self.setWindowIcon(QIcon.ic('filter.png'))
        self.l = l = QVBoxLayout(self)
        la = QLabel(_('Choose what kinds of highlights will be displayed, below. If none are selected, no filtering is performed.'))
        la.setWordWrap(True)
        l.addWidget(la)
        self.swatches = s = SwatchList(self.all_styles, self.show_only_styles, self)
        l.addWidget(s)
        l.addWidget(self.bb)
        self.bb.addButton(_('Select &all'), QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(s.select_all)
        self.bb.addButton(_('Select &none'), QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(s.select_none)

    @property
    def selected_styles(self):
        return frozenset(self.swatches.selected_styles)


class Export(ExportBase):
    prefs = vprefs
    pref_name = 'highlight_export_format'

    def file_type_data(self):
        return _('calibre highlights'), 'calibre_highlights'

    def initial_filename(self):
        return _('highlights')

    def exported_data(self):
        fmt = self.export_format.currentData()
        if fmt == 'calibre_highlights':
            return json.dumps({
                'version': 1,
                'type': 'calibre_highlights',
                'highlights': self.annotations,
            }, ensure_ascii=False, sort_keys=True, indent=2)
        lines = []
        as_markdown = fmt == 'md'
        link_prefix = link_prefix_for_location_links()
        root = ChapterGroup()
        for a in self.annotations:
            root.add_annot(a)
        root.render_as_text(lines, as_markdown, link_prefix)
        return '\n'.join(lines).strip()


class Highlights(QTreeWidget):

    jump_to_highlight = pyqtSignal(object)
    current_highlight_changed = pyqtSignal(object)
    delete_requested = pyqtSignal()
    edit_requested = pyqtSignal()
    edit_notes_requested = pyqtSignal()
    export_selected_requested = pyqtSignal()

    def __init__(self, parent=None):
        QTreeWidget.__init__(self, parent)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.styles_to_show = frozenset()
        self.default_decoration = QIcon.ic('blank.png')
        self.setHeaderHidden(True)
        self.num_of_items = 0
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        set_no_activate_on_click(self)
        self.itemActivated.connect(self.item_activated)
        self.currentItemChanged.connect(self.current_item_changed)
        self.uuid_map = {}
        self.section_font = QFont(self.font())
        self.section_font.setItalic(True)
        self.gesture_manager = GestureManager(self)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def viewportEvent(self, ev):
        if hasattr(self, 'gesture_manager'):
            ret = self.gesture_manager.handle_event(ev)
            if ret is not None:
                return ret
        return super().viewportEvent(ev)

    def show_context_menu(self, point):
        index = self.indexAt(point)
        h = index.data(highlight_role)
        self.context_menu = m = QMenu(self)
        if h is not None:
            m.addAction(QIcon.ic('edit_input.png'), _('Modify this highlight'), self.edit_requested.emit)
            m.addAction(QIcon.ic('modified.png'), _('Edit notes for this highlight'), self.edit_notes_requested.emit)
            m.addAction(QIcon.ic('trash.png'), ngettext(
                'Delete this highlight', 'Delete selected highlights', len(self.selectedItems())
            ), self.delete_requested.emit)
        m.addSeparator()
        if tuple(self.selected_highlights):
            m.addAction(QIcon.ic('save.png'), _('Export selected highlights'), self.export_selected_requested.emit)
        m.addAction(QIcon.ic('plus.png'), _('Expand all'), self.expandAll)
        m.addAction(QIcon.ic('minus.png'), _('Collapse all'), self.collapseAll)
        self.context_menu.popup(self.mapToGlobal(point))
        return True

    def current_item_changed(self, current, previous):
        self.current_highlight_changed.emit(current.data(0, highlight_role) if current is not None else None)

    def load(self, highlights, preserve_state=False):
        s = self.style()
        expanded_chapters = set()
        if preserve_state:
            root = self.invisibleRootItem()
            for i in range(root.childCount()):
                chapter = root.child(i)
                if chapter.isExpanded():
                    expanded_chapters.add(chapter.data(0, section_role))
        icon_size = s.pixelMetric(QStyle.PixelMetric.PM_SmallIconSize, None, self)
        dpr = self.devicePixelRatioF()
        is_dark = is_dark_theme()
        self.clear()
        self.uuid_map = {}
        highlights = (h for h in highlights if not h.get('removed') and h.get('highlighted_text'))
        smap = {}
        repeated_short_titles = defaultdict(set)

        @lru_cache
        def tooltip_for(tfam):
            tooltip = ''
            if len(tfam) > 1:
                lines = []
                for i, node in enumerate(tfam):
                    lines.append('\xa0\xa0' * i + '➤ ' + node)
                tooltip = ngettext('Table of Contents section:', 'Table of Contents sections:', len(lines))
                tooltip += '\n' + '\n'.join(lines)
            return tooltip

        for h in self.sorted_highlights(highlights):
            tfam = tuple(h.get('toc_family_titles') or ())
            spine_index = h.get('spine_index', -1)
            if tfam:
                tsec = tfam[0]
                lsec = tfam[-1]
                key = (spine_index,) + tfam
            else:
                tsec = h.get('top_level_section_title')
                lsec = h.get('lowest_level_section_title')
                key = (spine_index, tsec or '', lsec or '')
            short_title = lsec or tsec or _('Unknown')
            section = {
                'title': short_title, 'tfam': tfam, 'tsec': tsec, 'lsec': lsec, 'items': [], 'tooltip': tooltip_for(tfam), 'key': key,
            }
            smap.setdefault(key, section)['items'].append(h)
            repeated_short_titles[short_title].add(key)

        for keys in repeated_short_titles.values():
            if len(keys) > 1:
                for key in keys:
                    section = smap[key]
                    if section['tfam']:
                        section['title'] = ' ➤ '.join(section['tfam'])
                    elif section['tsec'] and section['lsec']:
                        section['title'] = ' ➤ '.join((section['tsec'], section['lsec']))

        for secnum, (sec_key, sec) in enumerate(smap.items()):
            section = QTreeWidgetItem([sec['title']], 1)
            section.setFlags(Qt.ItemFlag.ItemIsEnabled)
            section.setFont(0, self.section_font)
            section.setData(0, section_role, sec['key'])
            if sec['tooltip']:
                section.setToolTip(0, sec['tooltip'])
            self.addTopLevelItem(section)
            section.setExpanded(not preserve_state or sec['key'] in expanded_chapters)
            for itemnum, h in enumerate(sec['items']):
                txt = h.get('highlighted_text')
                txt = txt.replace('\n', ' ')
                if h.get('notes'):
                    txt = '•' + txt
                if len(txt) > 100:
                    txt = txt[:100] + '…'
                item = QTreeWidgetItem(section, [txt], 2)
                item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemNeverHasChildren)
                item.setData(0, highlight_role, h)
                try:
                    dec = decoration_for_style(self.palette(), h.get('style') or {}, icon_size, dpr, is_dark)
                except Exception:
                    import traceback
                    traceback.print_exc()
                    dec = None
                if dec is None:
                    dec = self.default_decoration
                item.setData(0, Qt.ItemDataRole.DecorationRole, dec)
                self.uuid_map[h['uuid']] = secnum, itemnum
                self.num_of_items += 1
        self.apply_filters()

    def sorted_highlights(self, highlights):
        def_idx = 999999999999999
        defval = def_idx, cfi_sort_key('/99999999')

        def cfi_key(h):
            cfi = h.get('start_cfi')
            si = h.get('spine_index', def_idx)
            return (si, cfi_sort_key(cfi)) if cfi else defval

        return sorted(highlights, key=cfi_key)

    def refresh(self, highlights):
        h = self.current_highlight
        self.load(highlights, preserve_state=True)
        if h is not None:
            idx = self.uuid_map.get(h['uuid'])
            if idx is not None:
                sec_idx, item_idx = idx
                self.set_current_row(sec_idx, item_idx)

    def iteritems(self):
        root = self.invisibleRootItem()
        for i in range(root.childCount()):
            sec = root.child(i)
            for k in range(sec.childCount()):
                yield sec.child(k)

    def count(self):
        return self.num_of_items

    def find_query(self, query):
        pat = query.regex
        items = tuple(self.iteritems())
        count = len(items)
        cr = -1
        ch = self.current_highlight
        if ch:
            q = ch['uuid']
            for i, item in enumerate(items):
                h = item.data(0, highlight_role)
                if h['uuid'] == q:
                    cr = i
        if query.backwards:
            if cr < 0:
                cr = count
            indices = chain(range(cr - 1, -1, -1), range(count - 1, cr, -1))
        else:
            if cr < 0:
                cr = -1
            indices = chain(range(cr + 1, count), range(cr + 1))
        for i in indices:
            h = items[i].data(0, highlight_role)
            if pat.search(h['highlighted_text']) is not None or pat.search(h.get('notes') or '') is not None:
                self.set_current_row(*self.uuid_map[h['uuid']])
                return True
        return False

    def find_annot_id(self, annot_id):
        q = self.uuid_map.get(annot_id)
        if q is not None:
            self.set_current_row(*q)
            return True
        return False

    def set_current_row(self, sec_idx, item_idx):
        sec = self.topLevelItem(sec_idx)
        if sec is not None:
            item = sec.child(item_idx)
            if item is not None:
                self.setCurrentItem(item, 0, QItemSelectionModel.SelectionFlag.ClearAndSelect)
                return True
        return False

    def item_activated(self, item):
        h = item.data(0, highlight_role)
        if h is not None:
            self.jump_to_highlight.emit(h)

    @property
    def current_highlight(self):
        i = self.currentItem()
        if i is not None:
            return i.data(0, highlight_role)

    @property
    def all_highlights(self):
        for item in self.iteritems():
            yield item.data(0, highlight_role)

    @property
    def selected_highlights(self):
        for item in self.selectedItems():
            yield item.data(0, highlight_role)

    @property
    def all_highlight_styles(self):
        seen = set()
        for h in self.all_highlights:
            s = h.get('style')
            if h.get('removed') or h.get('type') != 'highlight' or not s:
                continue
            k = compute_style_key(s)
            if k not in seen:
                yield s
                seen.add(k)

    def apply_filters(self):
        q = self.styles_to_show
        for item in self.iteritems():
            h = item.data(0, highlight_role)
            hidden = False
            if q:
                skey = compute_style_key(h.get('style', {}))
                hidden = skey not in q
            item.setHidden(hidden)
        root = self.invisibleRootItem()
        for i in range(root.childCount()):
            sec = root.child(i)
            for k in range(sec.childCount()):
                if not sec.child(k).isHidden():
                    sec.setHidden(False)
                    break
            else:
                sec.setHidden(True)

    def keyPressEvent(self, ev):
        if ev.matches(QKeySequence.StandardKey.Delete):
            self.delete_requested.emit()
            ev.accept()
            return
        if ev.key() == Qt.Key.Key_F2:
            self.edit_requested.emit()
            ev.accept()
            return
        return super().keyPressEvent(ev)


class NotesEditDialog(Dialog):

    def __init__(self, notes, parent=None):
        self.initial_notes = notes
        Dialog.__init__(self, name='edit-notes-highlight', title=_('Edit notes'), parent=parent)

    def setup_ui(self):
        l = QVBoxLayout(self)
        self.qte = qte = QTextEdit(self)
        qte.setMinimumHeight(400)
        qte.setMinimumWidth(600)
        if self.initial_notes:
            qte.setPlainText(self.initial_notes)
            qte.moveCursor(QTextCursor.MoveOperation.End)
        l.addWidget(qte)
        l.addWidget(self.bb)

    @property
    def notes(self):
        return self.qte.toPlainText().rstrip()


class NotesDisplay(Details):

    notes_edited = pyqtSignal(object)

    def __init__(self, parent=None):
        Details.__init__(self, parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.anchorClicked.connect(self.anchor_clicked)
        self.current_notes = ''

    def show_notes(self, text=''):
        text = (text or '').strip()
        self.setVisible(bool(text))
        self.current_notes = text
        html = '\n'.join(render_notes(text))
        self.setHtml('<div><a href="edit://moo">{}</a></div>{}'.format(_('Edit notes'), html))
        self.document().setDefaultStyleSheet('a[href] { text-decoration: none }')
        h = self.document().size().height() + 2
        self.setMaximumHeight(int(h))

    def anchor_clicked(self, qurl):
        if qurl.scheme() == 'edit':
            self.edit_notes()
        else:
            safe_open_url(qurl)

    def edit_notes(self):
        current_text = self.current_notes
        d = NotesEditDialog(current_text, self)
        if d.exec() == QDialog.DialogCode.Accepted and d.notes != current_text:
            self.notes_edited.emit(d.notes)


class HighlightsPanel(QWidget):

    request_highlight_action = pyqtSignal(object, object)
    web_action = pyqtSignal(object, object)
    toggle_requested = pyqtSignal()
    notes_edited_signal = pyqtSignal(object, object)

    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.l = l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        self.search_input = si = SearchInput(self, 'highlights-search', show_return_button=False)
        si.do_search.connect(self.search_requested)
        l.addWidget(si)

        h = QHBoxLayout()
        la = QLabel(_('Double click to jump to an entry'))
        self.filter_button = b = QToolButton(self)
        b.setIcon(QIcon.ic('filter.png')), b.setToolTip(_('Show only highlights of specific types'))
        b.clicked.connect(self.change_active_filter)
        h.addWidget(la), h.addStretch(10), h.addWidget(b)
        l.addLayout(h)

        self.highlights = h = Highlights(self)
        l.addWidget(h)
        h.jump_to_highlight.connect(self.jump_to_highlight)
        h.delete_requested.connect(self.remove_highlight)
        h.edit_requested.connect(self.edit_highlight)
        h.edit_notes_requested.connect(self.edit_notes)
        h.current_highlight_changed.connect(self.current_highlight_changed)
        h.export_selected_requested.connect(self.export_selected)
        self.load = h.load
        self.refresh = h.refresh

        self.h = h = QHBoxLayout()

        def button(icon, text, tt, target):
            b = QPushButton(QIcon.ic(icon), text, self)
            b.setToolTip(tt)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(target)
            return b

        self.edit_button = button('edit_input.png', _('Modify'), _('Modify the selected highlight'), self.edit_highlight)
        self.remove_button = button('trash.png', _('Delete'), _('Delete the selected highlights'), self.remove_highlight)
        self.export_button = button('save.png', _('Export'), _('Export all highlights'), self.export)
        h.addWidget(self.edit_button), h.addWidget(self.remove_button), h.addWidget(self.export_button)

        self.notes_display = nd = NotesDisplay(self)
        nd.notes_edited.connect(self.notes_edited)
        l.addWidget(nd)
        nd.setVisible(False)
        l.addLayout(h)

    def change_active_filter(self):
        d = FilterDialog(self.highlights.all_highlight_styles, self.highlights.styles_to_show, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.highlights.styles_to_show = d.selected_styles
            self.highlights.apply_filters()

    def notes_edited(self, text):
        h = self.highlights.current_highlight
        if h is not None:
            h['notes'] = text
            self.web_action.emit('set-notes-in-highlight', h)
            self.notes_edited_signal.emit(h['uuid'], text)

    def set_tooltips(self, rmap):
        a = rmap.get('create_annotation')
        if a:

            def as_text(idx):
                return index_to_key_sequence(idx).toString(QKeySequence.SequenceFormat.NativeText)

            tt = self.add_button.toolTip().partition('[')[0].strip()
            keys = sorted(filter(None, map(as_text, a)))
            if keys:
                self.add_button.setToolTip('{} [{}]'.format(tt, ', '.join(keys)))

    def search_requested(self, query):
        if not self.highlights.find_query(query):
            error_dialog(self, _('No matches'), _(
                'No highlights match the search: {}').format(query.text), show=True)

    def focus(self):
        self.highlights.setFocus(Qt.FocusReason.OtherFocusReason)

    def jump_to_highlight(self, highlight):
        boss = get_boss()
        if boss.check_for_read_aloud(_('the location of this highlight')):
            return
        self.request_highlight_action.emit(highlight['uuid'], 'goto')

    def current_highlight_changed(self, highlight):
        nd = self.notes_display
        if highlight is None or not highlight.get('notes'):
            nd.show_notes()
        else:
            nd.show_notes(highlight['notes'])

    def no_selected_highlight(self):
        error_dialog(self, _('No selected highlight'), _(
            'No highlight is currently selected'), show=True)

    def edit_highlight(self):
        boss = get_boss()
        if boss.check_for_read_aloud(_('the location of this highlight')):
            return
        h = self.highlights.current_highlight
        if h is None:
            return self.no_selected_highlight()
        self.request_highlight_action.emit(h['uuid'], 'edit')

    def edit_notes(self):
        self.notes_display.edit_notes()

    def remove_highlight(self):
        highlights = tuple(self.highlights.selected_highlights)
        if not highlights:
            return self.no_selected_highlight()
        if confirm(
            ngettext(
            'Are you sure you want to delete this highlight permanently?',
            'Are you sure you want to delete all {} highlights permanently?',
            len(highlights)).format(len(highlights)),
            'delete-highlight-from-viewer', parent=self, config_set=vprefs
        ):
            for h in highlights:
                self.request_highlight_action.emit(h['uuid'], 'delete')

    def export(self):
        hl = list(self.highlights.all_highlights)
        if not hl:
            return error_dialog(self, _('No highlights'), _('This book has no highlights to export'), show=True)
        Export(hl, self).exec()

    def export_selected(self):
        hl = list(self.highlights.selected_highlights)
        if not hl:
            return error_dialog(self, _('No highlights'), _('No highlights selected to export'), show=True)
        Export(hl, self).exec()

    def selected_text_changed(self, text, annot_id):
        if annot_id:
            self.highlights.find_annot_id(annot_id)

    def keyPressEvent(self, ev):
        sc = get_shortcut_for(self, ev)
        if sc == 'toggle_highlights' or ev.key() == Qt.Key.Key_Escape:
            self.toggle_requested.emit()
        return super().keyPressEvent(ev)
