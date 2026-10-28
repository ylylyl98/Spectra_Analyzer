import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "projectNavigator"
    required property var project
    // The native QuickPanel supplies its inherited QWidget font and updates it
    // on font changes, including QApplication.setFont and accessibility scaling.
    property font controlFont: Qt.application.font
    implicitWidth: 300
    implicitHeight: 520
    color: colors.surface
    Accessible.name: "Project navigator"

    // These are viewport offsets, not selection or expansion state. Model resets
    // must not make a refresh reveal the current document or lose a filter's place.
    property real resetScrollOffset: 0
    property real beforeFilterScrollOffset: 0
    property string previousSearch: ""

    AppColors { id: colors }

    function actionText(action) {
        return action ? action.text.replace(/&(?!&)/g, "").replace(/&&/g, "&") : "";
    }

    function openContext(key, item, x, y) {
        if (!key.length)
            return;
        projectList.forceActiveFocus(Qt.MouseFocusReason);
        root.project.prepareContext(key);
        contextMenu.popup(item, x, y);
    }

    function openCurrentContext() {
        if (!root.project.currentKey.length)
            return;
        let row = projectList.itemAtIndex(root.project.currentIndex);
        if (row)
            openContext(root.project.currentKey, row, Math.min(row.width - 4, 36 + row.depth * 12), row.height);
        else
            openContext(root.project.currentKey, projectList, 12, 20);
    }

    function restoreViewport() {
        projectList.forceLayout();
        projectList.contentY = Math.max(projectList.originY,
            projectList.originY + Math.min(root.resetScrollOffset, Math.max(0, projectList.contentHeight - projectList.height)));
    }

    ColumnLayout {
        id: heading
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 8
        spacing: 7

        RowLayout {
            Layout.fillWidth: true
            spacing: 4

            Text {
                text: "Project"
                Layout.fillWidth: true
                color: colors.text
                font.family: root.controlFont.family
                font.pointSize: root.controlFont.pointSize > 0 ? root.controlFont.pointSize : 9
                font.weight: Font.DemiBold
                Accessible.role: Accessible.Heading
            }

            SurfaceButton {
                id: importButton
                objectName: "projectImport"
                text: "Import"
                quiet: true
                leftPadding: 8
                rightPadding: 8
                enabled: root.project.enabled && root.project.importAction !== null
                         && root.project.importAction.enabled
                Accessible.name: "Import data into project"
                Accessible.description: root.project.importAction ? root.project.importAction.tooltip : ""
                onClicked: root.project.importAction.trigger()
                Controls.ToolTip.visible: hovered && Accessible.description.length > 0
                Controls.ToolTip.text: Accessible.description
                Controls.ToolTip.delay: 650
            }

            SurfaceButton {
                id: actionsButton
                objectName: "projectActions"
                text: "Actions"
                quiet: true
                leftPadding: 8
                rightPadding: 8
                enabled: root.project.enabled
                Accessible.name: "Project actions"
                Accessible.description: "Open commands for the selected project items."
                onClicked: actionsMenu.popup(actionsButton, 0, actionsButton.height)
                Controls.ToolTip.visible: hovered
                Controls.ToolTip.text: Accessible.description
                Controls.ToolTip.delay: 650
            }
        }

        Controls.TextField {
            id: searchField
            objectName: "projectSearch"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            implicitHeight: 32
            enabled: root.project.enabled
            placeholderText: "Search project"
            selectByMouse: true
            leftPadding: 29
            rightPadding: clearSearch.visible ? 32 : 9
            topPadding: 5
            bottomPadding: 5
            color: enabled ? colors.text : colors.disabledText
            placeholderTextColor: colors.secondary
            selectionColor: colors.accent
            selectedTextColor: colors.surface
            font.family: root.controlFont.family
            font.pointSize: root.controlFont.pointSize > 0 ? root.controlFont.pointSize : 9
            Accessible.name: "Search project"
            Accessible.description: "Filter sources, worksheets, heatmaps, and comparisons by their full name or source path."
            onTextEdited: root.project.setSearchText(text)
            Binding on text { value: root.project.searchText }

            Keys.onDownPressed: function(event) {
                projectList.forceActiveFocus(Qt.TabFocusReason);
                if (root.project.currentIndex < 0)
                    root.project.navigate("home", event.modifiers);
                event.accepted = true;
            }

            background: Rectangle {
                radius: 4
                color: searchField.enabled ? colors.surface : colors.disabled
                border.width: searchField.activeFocus ? 2 : 1
                border.color: searchField.activeFocus ? colors.accent : colors.border
            }

            Item {
                x: 9
                y: (searchField.height - 14) / 2
                width: 14
                height: 14
                Rectangle {
                    x: 0
                    y: 0
                    width: 10
                    height: 10
                    radius: 5
                    color: "transparent"
                    border.width: 1.3
                    border.color: searchField.enabled ? colors.secondary : colors.disabledText
                }
                Rectangle {
                    x: 9
                    y: 8
                    width: 6
                    height: 1.3
                    rotation: 45
                    transformOrigin: Item.Left
                    color: searchField.enabled ? colors.secondary : colors.disabledText
                }
            }

            SurfaceButton {
                id: clearSearch
                objectName: "projectSearchClear"
                anchors.right: parent.right
                anchors.rightMargin: 3
                anchors.verticalCenter: parent.verticalCenter
                width: 26
                height: 26
                quiet: true
                visible: searchField.text.length > 0
                enabled: searchField.enabled
                text: ""
                Accessible.name: "Clear project search"
                onClicked: {
                    root.project.setSearchText("");
                    searchField.forceActiveFocus(Qt.MouseFocusReason);
                }
                contentItem: Item {
                    Rectangle {
                        anchors.centerIn: parent
                        width: 11
                        height: 1.3
                        rotation: 45
                        color: clearSearch.enabled ? colors.secondary : colors.disabledText
                    }
                    Rectangle {
                        anchors.centerIn: parent
                        width: 11
                        height: 1.3
                        rotation: -45
                        color: clearSearch.enabled ? colors.secondary : colors.disabledText
                    }
                }
                Controls.ToolTip.visible: hovered
                Controls.ToolTip.text: Accessible.name
                Controls.ToolTip.delay: 650
            }
        }
    }

    ListView {
        id: projectList
        objectName: "projectList"
        anchors.top: heading.bottom
        anchors.topMargin: 8
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 6
        anchors.rightMargin: 6
        anchors.bottomMargin: 6
        clip: true
        model: root.project.model
        currentIndex: -1
        reuseItems: true
        cacheBuffer: 160
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        activeFocusOnTab: true
        keyNavigationEnabled: false
        Accessible.role: Accessible.Tree
        Accessible.name: "Project items"
        Accessible.description: "Use the arrow keys to select or expand items. Enter opens an item. Control and Shift extend selection."

        Controls.ScrollBar.vertical: Controls.ScrollBar {
            id: verticalBar
            objectName: "projectScrollBar"
            policy: Controls.ScrollBar.AsNeeded
            width: 10
        }

        Keys.priority: Keys.BeforeItem
        Keys.onPressed: function(event) {
            let command = "";
            switch (event.key) {
            case Qt.Key_Up: command = "up"; break;
            case Qt.Key_Down: command = "down"; break;
            case Qt.Key_Home: command = "home"; break;
            case Qt.Key_End: command = "end"; break;
            case Qt.Key_Left: command = root.project.enabled ? "left" : ""; break;
            case Qt.Key_Right: command = root.project.enabled ? "right" : ""; break;
            case Qt.Key_Return:
            case Qt.Key_Enter: command = root.project.enabled ? "enter" : ""; break;
            case Qt.Key_Space:
                root.project.select(root.project.currentKey, event.modifiers, false);
                event.accepted = true;
                return;
            case Qt.Key_Menu:
                root.openCurrentContext();
                event.accepted = true;
                return;
            case Qt.Key_F10:
                if (event.modifiers & Qt.ShiftModifier) {
                    root.openCurrentContext();
                    event.accepted = true;
                    return;
                }
                break;
            }
            if (command.length) {
                root.project.navigate(command, event.modifiers);
                event.accepted = true;
            }
        }

        delegate: Item {
            id: row
            required property string rowKey
            required property string label
            required property string tooltip
            required property string description
            required property int depth
            required property string kind
            required property bool hasChildren
            required property bool expanded
            required property bool selected
            required property bool current
            required property bool active
            required property bool sectionStart
            readonly property real sectionSpace: sectionStart ? 7 : 0
            objectName: "projectRow_" + rowKey
            width: projectList.width - verticalBar.width - 4
            height: Math.max(32, rowLabel.implicitHeight + 12) + sectionSpace
            Accessible.role: Accessible.TreeItem
            Accessible.name: label
            Accessible.description: description
            Accessible.selected: selected
            Accessible.focusable: true
            Accessible.onPressAction: {
                projectList.forceActiveFocus(Qt.OtherFocusReason);
                root.project.select(rowKey, 0, false);
            }

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                y: 2
                height: 1
                color: colors.divider
                visible: row.sectionStart
            }

            Rectangle {
                id: rowBody
                y: row.sectionSpace
                width: parent.width
                height: parent.height - y
                radius: 3
                color: rowMouse.pressed ? colors.pressed
                       : row.selected ? colors.selected
                       : rowMouse.containsMouse ? colors.hover : "transparent"
                border.width: row.current ? 1 : 0
                border.color: projectList.activeFocus ? colors.accent : colors.border

                Rectangle {
                    x: 0
                    y: 5
                    width: 2
                    height: parent.height - 10
                    radius: 1
                    color: colors.accent
                    visible: row.selected
                }

                ProjectItemIcon {
                    x: 22 + row.depth * 12
                    y: 8
                    width: 16
                    height: 16
                    kind: row.kind
                    hasChildren: row.hasChildren
                    expanded: row.expanded
                    iconColor: row.active ? colors.accent : colors.secondary
                    surfaceColor: colors.surface
                }

                Text {
                    id: rowLabel
                    objectName: "projectLabel_" + row.rowKey
                    x: 44 + row.depth * 12
                    y: 6
                    width: Math.max(1, rowBody.width - x - 7)
                    text: row.label
                    textFormat: Text.PlainText
                    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                    color: colors.text
                    font.family: root.controlFont.family
                    font.pointSize: root.controlFont.pointSize > 0 ? root.controlFont.pointSize : 9
                    font.weight: row.hasChildren || row.kind === "workbook" || row.kind === "group" || row.active
                                 ? Font.DemiBold : root.controlFont.weight
                    font.underline: row.active
                }

                MouseArea {
                    id: rowMouse
                    objectName: "projectHit_" + row.rowKey
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    hoverEnabled: true
                    onPressed: projectList.forceActiveFocus(Qt.MouseFocusReason)
                    onClicked: function(mouse) {
                        if (mouse.button === Qt.RightButton)
                            root.openContext(row.rowKey, rowBody, mouse.x, mouse.y);
                        else
                            root.project.select(row.rowKey, mouse.modifiers, false);
                    }
                    onDoubleClicked: function(mouse) {
                        if (mouse.button === Qt.LeftButton && root.project.enabled) {
                            if (row.kind === "group")
                                root.project.toggleExpanded(row.rowKey);
                            else
                                root.project.activate(row.rowKey);
                        }
                    }
                    Controls.ToolTip.visible: containsMouse && row.tooltip.length > 0
                    Controls.ToolTip.text: row.tooltip
                    Controls.ToolTip.delay: 650
                }

                Item {
                    x: 4 + row.depth * 12
                    width: 18
                    height: parent.height
                    visible: row.hasChildren

                    Item {
                        x: 3
                        y: 10
                        width: 12
                        height: 12
                        rotation: row.expanded ? 90 : 0
                        Rectangle {
                            x: 4
                            y: 2
                            width: 6
                            height: 1.4
                            radius: 0.7
                            rotation: 45
                            transformOrigin: Item.Left
                            color: root.project.enabled ? colors.secondary : colors.disabledText
                        }
                        Rectangle {
                            x: 4
                            y: 10
                            width: 6
                            height: 1.4
                            radius: 0.7
                            rotation: -45
                            transformOrigin: Item.Left
                            color: root.project.enabled ? colors.secondary : colors.disabledText
                        }
                    }

                    MouseArea {
                        objectName: "projectExpand_" + row.rowKey
                        anchors.fill: parent
                        enabled: root.project.enabled
                        onClicked: {
                            projectList.forceActiveFocus(Qt.MouseFocusReason);
                            root.project.toggleExpanded(row.rowKey);
                        }
                        // The first click already toggles. Suppress the second
                        // release of a double click so a branch changes once.
                        onDoubleClicked: function(mouse) { mouse.accepted = true; }
                    }
                }
            }
        }

        Text {
            objectName: "projectEmpty"
            anchors.centerIn: parent
            width: Math.max(1, parent.width - 24)
            visible: projectList.count === 0
            text: root.project.searchText.length ? "No matching project items" : "Import data to start a project"
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            horizontalAlignment: Text.AlignHCenter
            color: colors.secondary
            font.family: root.controlFont.family
            font.pointSize: root.controlFont.pointSize > 0 ? root.controlFont.pointSize : 9
        }
    }

    Component {
        id: commandMenuItem
        Controls.MenuItem {
            id: menuCommand
            required property var modelData
            objectName: "projectCommand_" + modelData.key
            text: root.actionText(modelData)
            enabled: root.project.enabled && modelData.enabled
            checkable: modelData.checkable
            hoverEnabled: true
            implicitHeight: Math.max(32, commandLabel.implicitHeight + 12)
            leftPadding: checkable ? 28 : 10
            rightPadding: 10
            Accessible.name: text
            Accessible.description: modelData.tooltip
            onTriggered: modelData.trigger()
            Binding on checked { value: menuCommand.modelData.checked }
            contentItem: Text {
                id: commandLabel
                text: menuCommand.text
                textFormat: Text.PlainText
                wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                color: menuCommand.enabled ? colors.text : colors.disabledText
                font.family: root.controlFont.family
                font.pointSize: root.controlFont.pointSize > 0 ? root.controlFont.pointSize : 9
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                radius: 3
                color: menuCommand.down ? colors.pressed
                       : menuCommand.highlighted ? colors.selected : "transparent"
                border.width: menuCommand.visualFocus ? 1 : 0
                border.color: colors.accent
            }
            Controls.ToolTip.visible: hovered && modelData.tooltip.length > 0
            Controls.ToolTip.text: modelData.tooltip
            Controls.ToolTip.delay: 650
        }
    }

    Controls.Menu {
        id: actionsMenu
        objectName: "projectActionsMenu"
        width: Math.max(120, root.width - 12)
        height: Math.min(implicitHeight, Math.max(32, root.height - 12))
        margins: 6
        padding: 4
        onClosed: actionsButton.forceActiveFocus(Qt.PopupFocusReason)
        contentItem: ListView {
            id: actionsList
            implicitHeight: contentHeight
            model: actionsMenu.contentModel
            currentIndex: actionsMenu.currentIndex
            interactive: contentHeight > height
            boundsBehavior: Flickable.StopAtBounds
            clip: true
            onCurrentIndexChanged: Qt.callLater(function() {
                actionsList.forceLayout();
                actionsList.positionViewAtIndex(actionsList.currentIndex, ListView.Contain);
            })
            Controls.ScrollBar.vertical: Controls.ScrollBar {
                width: 8
                policy: Controls.ScrollBar.AsNeeded
            }
        }
        background: Rectangle {
            color: colors.surface
            radius: 4
            border.color: colors.border
        }
        Instantiator {
            model: root.project.actions
            delegate: commandMenuItem
            onObjectAdded: function(index, object) { actionsMenu.insertItem(index, object); }
            onObjectRemoved: function(index, object) { actionsMenu.removeItem(object); }
        }
    }

    Controls.Menu {
        id: contextMenu
        objectName: "projectContextMenu"
        width: Math.max(120, root.width - 12)
        height: Math.min(implicitHeight, Math.max(32, root.height - 12))
        margins: 6
        padding: 4
        onClosed: projectList.forceActiveFocus(Qt.PopupFocusReason)
        contentItem: ListView {
            id: contextList
            implicitHeight: contentHeight
            model: contextMenu.contentModel
            currentIndex: contextMenu.currentIndex
            interactive: contentHeight > height
            boundsBehavior: Flickable.StopAtBounds
            clip: true
            onCurrentIndexChanged: Qt.callLater(function() {
                contextList.forceLayout();
                contextList.positionViewAtIndex(contextList.currentIndex, ListView.Contain);
            })
            Controls.ScrollBar.vertical: Controls.ScrollBar {
                width: 8
                policy: Controls.ScrollBar.AsNeeded
            }
        }
        background: Rectangle {
            color: colors.surface
            radius: 4
            border.color: colors.border
        }
        Controls.MenuItem {
            objectName: "projectContextToggle"
            visible: root.project.contextIsGroup
            height: visible ? implicitHeight : 0
            text: root.project.contextExpanded ? "Collapse" : "Expand"
            enabled: root.project.enabled
            onTriggered: root.project.toggleContextGroup()
        }
        Instantiator {
            model: root.project.contextIsGroup ? [] : root.project.contextActions
            delegate: commandMenuItem
            onObjectAdded: function(index, object) { contextMenu.insertItem(index, object); }
            onObjectRemoved: function(index, object) { contextMenu.removeItem(object); }
        }
    }

    Connections {
        target: root.project.model
        function onModelAboutToBeReset() {
            root.resetScrollOffset = projectList.contentY - projectList.originY;
            let search = root.project.searchText;
            if (!root.previousSearch.length && search.length)
                root.beforeFilterScrollOffset = root.resetScrollOffset;
            else if (root.previousSearch.length && !search.length)
                root.resetScrollOffset = root.beforeFilterScrollOffset;
            root.previousSearch = search;
        }
        function onModelReset() {
            Qt.callLater(function() {
                root.restoreViewport();
                Qt.callLater(function() {
                    root.restoreViewport();
                });
            });
        }
    }

    Connections {
        target: root.project
        function onRevealRequested(visibleIndex) {
            Qt.callLater(function() {
                projectList.forceLayout();
                projectList.positionViewAtIndex(visibleIndex, ListView.Contain);
                // A virtualized target may acquire its wrapped height only
                // after the first positioning pass creates its delegate.
                Qt.callLater(function() {
                    projectList.forceLayout();
                    projectList.positionViewAtIndex(visibleIndex, ListView.Contain);
                });
            });
        }
    }
}
