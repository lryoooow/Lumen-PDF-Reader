"""功能区在窄窗口中仍能找到动作，且不复制或误触发 QAction。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QToolButton

from pdfreader.gui.theme import ThemeManager
from pdfreader.gui.widgets.ribbon import Ribbon


def _make_ribbon(qtbot) -> tuple[Ribbon, list[QAction]]:  # type: ignore[no-untyped-def]
    ribbon = Ribbon(ThemeManager("light"))
    qtbot.addWidget(ribbon)
    actions = [QAction(f"第 {i + 1} 个操作", ribbon) for i in range(15)]
    ribbon.add_tab("测试", [("文件", actions[:5]), ("整理", actions[5:10]), ("输出", actions[10:])])
    # 固定逻辑宽度，避免系统字体或真实显示器的最大宽度改变测试所需边界。
    for action in actions:
        ribbon.button_for(action).setMinimumWidth(90)
    ribbon.finish()
    ribbon.resize(1024, 138)
    ribbon.show()
    QApplication.processEvents()
    return ribbon, actions


def _overflow(ribbon: Ribbon) -> QToolButton:
    button = ribbon.findChild(QToolButton, "RibbonOverflowButton")
    assert button is not None, "窄窗口需要有明确的“更多”入口"
    return button


def _open_overflow(ribbon: Ribbon) -> QMenu:
    button = _overflow(ribbon)
    menu = button.menu()
    assert menu is not None
    menu.popup(button.mapToGlobal(QPoint(0, button.height())))
    QApplication.processEvents()
    return menu


def test_narrow_ribbon_keeps_every_action_reachable_and_restores_width(qtbot) -> None:  # type: ignore[no-untyped-def]
    ribbon, actions = _make_ribbon(qtbot)
    assert ribbon.width() == 1024, "工具栏不能把父窗口最小宽度撑大"
    overflow = _overflow(ribbon)
    assert overflow.isVisibleTo(ribbon)
    assert ribbon.rect().contains(QRect(overflow.mapTo(ribbon, QPoint()), overflow.size()))
    menu = _open_overflow(ribbon)
    visible = [a for a in actions if ribbon.button_for(a).isVisibleTo(ribbon)]
    hidden = [a for a in actions if a in menu.actions()]
    assert visible and hidden
    assert set(visible).isdisjoint(hidden)
    assert set(visible + hidden) == set(actions)
    menu.close()

    ribbon.resize(1800, 138)
    qtbot.waitUntil(lambda: not overflow.isVisibleTo(ribbon))
    assert not overflow.isVisibleTo(ribbon)
    assert all(ribbon.button_for(a).isVisibleTo(ribbon) for a in actions)
    assert [label.text() for label in ribbon.group_titles(0)] == ["文件", "整理", "输出"]


def test_overflow_uses_original_actions_and_disabled_action_cannot_run(qtbot) -> None:  # type: ignore[no-untyped-def]
    ribbon, actions = _make_ribbon(qtbot)
    disabled, checked = actions[-2:]
    disabled.setEnabled(False)
    checked.setCheckable(True)
    checked.setChecked(True)
    calls: list[str] = []
    disabled.triggered.connect(lambda: calls.append("disabled"))
    checked.triggered.connect(lambda: calls.append("checked"))
    menu = _open_overflow(ribbon)
    assert disabled in menu.actions() and not disabled.isEnabled()
    assert checked in menu.actions() and checked.isChecked()
    menu.setActiveAction(disabled)
    qtbot.keyClick(menu, Qt.Key.Key_Return)
    assert calls == []
    menu.setActiveAction(checked)
    qtbot.keyClick(menu, Qt.Key.Key_Return)
    assert calls == ["checked"]
    assert not checked.isChecked()


def test_overflow_preserves_dropdown_menu_and_single_leaf_trigger(qtbot) -> None:  # type: ignore[no-untyped-def]
    ribbon, actions = _make_ribbon(qtbot)
    dropdown = actions[-1]
    submenu = QMenu(ribbon)
    leaf = submenu.addAction("仅当前页")
    calls: list[int] = []
    leaf.triggered.connect(lambda: calls.append(1))
    ribbon.set_menu(dropdown, submenu)
    menu = _open_overflow(ribbon)
    assert dropdown in menu.actions()
    assert dropdown.menu() is submenu
    assert ribbon.button_for(dropdown).menu() is submenu
    menu.close()
    submenu.popup(ribbon.mapToGlobal(QPoint(10, 10)))
    submenu.setActiveAction(leaf)
    qtbot.keyClick(submenu, Qt.Key.Key_Return)
    assert calls == [1]

    replacement = QMenu(ribbon)
    replacement.addAction("全部页")
    ribbon.set_menu(dropdown, replacement)
    QApplication.processEvents()
    assert ribbon.button_for(dropdown).defaultAction() is dropdown
    assert ribbon.button_for(dropdown).menu() is replacement
    assert dropdown.menu() is replacement
    menu = _open_overflow(ribbon)
    assert dropdown in menu.actions()
    menu.close()


def test_keyboard_focus_can_reach_tools_and_activate_once(qtbot) -> None:  # type: ignore[no-untyped-def]
    ribbon, actions = _make_ribbon(qtbot)
    button = ribbon.button_for(actions[0])
    assert button is not None
    assert button.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert _overflow(ribbon).focusPolicy() & Qt.FocusPolicy.TabFocus
    calls: list[int] = []
    actions[0].triggered.connect(lambda: calls.append(1))
    button.setFocus(Qt.FocusReason.TabFocusReason)
    qtbot.waitUntil(button.hasFocus)
    qtbot.keyClick(button, Qt.Key.Key_Space)
    assert calls == [1]
    button.grab()  # 焦点轮廓的绘制也必须可运行。
    qtbot.keyClick(button, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is not button


def test_repeated_reflow_and_action_visibility_do_not_duplicate_actions(qtbot) -> None:  # type: ignore[no-untyped-def]
    ribbon, actions = _make_ribbon(qtbot)
    menu = _open_overflow(ribbon)
    menu.close()
    menus_before = len(ribbon.findChildren(QMenu))
    for _ in range(5):
        ribbon.resize(1500, 138)
        QApplication.processEvents()
        ribbon.resize(1024, 138)
        QApplication.processEvents()
        menu = _open_overflow(ribbon)
        external = [a for a in menu.actions() if not a.isSeparator()]
        assert len(external) == len(set(external))
        menu.close()
    assert len(ribbon.findChildren(QMenu)) == menus_before
    actions[-1].setVisible(False)
    QApplication.processEvents()
    menu = _open_overflow(ribbon)
    assert actions[-1] not in menu.actions()
    assert not ribbon.button_for(actions[-1]).isVisibleTo(ribbon)
    menu.close()


def test_hidden_action_keeps_its_shortcut_without_opening_overflow(qtbot) -> None:  # type: ignore[no-untyped-def]
    ribbon, actions = _make_ribbon(qtbot)
    target = actions[-1]
    target.setShortcut("F6")
    calls: list[int] = []
    target.triggered.connect(lambda: calls.append(1))
    QApplication.processEvents()
    assert not ribbon.button_for(target).isVisibleTo(ribbon)
    ribbon.activateWindow()
    ribbon.button_for(actions[0]).setFocus()
    QApplication.processEvents()
    qtbot.keyClick(ribbon, Qt.Key.Key_F6)
    assert calls == [1]
    target.setEnabled(False)
    qtbot.keyClick(ribbon, Qt.Key.Key_F6)
    assert calls == [1]


def test_focus_moves_to_overflow_when_focused_tool_is_hidden(qtbot) -> None:  # type: ignore[no-untyped-def]
    ribbon, actions = _make_ribbon(qtbot)
    ribbon.resize(1800, 138)
    button = ribbon.button_for(actions[-1])
    qtbot.waitUntil(lambda: button.isVisibleTo(ribbon))
    button.setFocus(Qt.FocusReason.TabFocusReason)
    qtbot.waitUntil(button.hasFocus)
    ribbon.resize(1024, 138)
    QApplication.processEvents()
    assert _overflow(ribbon).hasFocus()


def test_switch_tabs_and_changed_labels_reflow_without_forcing_width(qtbot) -> None:  # type: ignore[no-untyped-def]
    ribbon, actions = _make_ribbon(qtbot)
    ribbon.add_tab("第二页", [("当前页", [actions[-1]])])
    ribbon._tab_group.button(1).click()
    QApplication.processEvents()
    assert (
        not ribbon._stack.currentWidget()
        .findChild(QToolButton, "RibbonOverflowButton")
        .isVisibleTo(ribbon)
    )
    ribbon.group_titles(1)[0].setText("很长的当前页标题" * 30)
    QApplication.processEvents()
    QApplication.processEvents()
    assert ribbon.width() == 1024
    more = ribbon._stack.currentWidget().findChild(QToolButton, "RibbonOverflowButton")
    assert more.isVisibleTo(ribbon)
    menu = more.menu()
    menu.popup(more.mapToGlobal(QPoint(0, more.height())))
    assert actions[-1] in menu.actions()
    menu.close()


def test_header_tabs_and_commands_are_keyboard_accessible(qtbot) -> None:  # type: ignore[no-untyped-def]
    ribbon, actions = _make_ribbon(qtbot)
    ribbon.add_tab("第二页", [("文件", actions[:2])])
    tab = ribbon._tab_group.button(1)
    for button in (ribbon._brand, ribbon._theme_btn, ribbon._home_btn, tab):
        assert button.focusPolicy() & Qt.FocusPolicy.TabFocus
        button.setFocus(Qt.FocusReason.TabFocusReason)
        qtbot.waitUntil(button.hasFocus)
        button.grab()
    qtbot.keyClick(tab, Qt.Key.Key_Space)
    assert ribbon.current_index() == 1
