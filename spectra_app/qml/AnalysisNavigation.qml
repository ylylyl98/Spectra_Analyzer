import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "AnalysisNavigation"
    required property var bridge
    property font controlFont: Qt.application.font
    implicitWidth: 480
    implicitHeight: 40
    color: colors.surface
    Accessible.name: "Analysis plot navigation"
    AppColors { id: colors }

    function action(key) {
        const rows = bridge.actions
        for (let i = 0; i < rows.length; ++i)
            if (rows[i].key === key) return rows[i]
        return {text: "", tooltip: "", enabled: false}
    }

    component PlainTip: Controls.ToolTip {
        id: tip
        popupType: Controls.Popup.Window
        delay: 650
        font: root.controlFont
        contentItem: Text {
            text: tip.text
            textFormat: Text.PlainText
            font: root.controlFont
            color: colors.text
            wrapMode: Text.WrapAnywhere
        }
        background: Rectangle { color: colors.surface; border.color: colors.border; radius: 4 }
    }

    component NavButton: SurfaceButton {
        id: button
        property string command: ""
        property string actionOwner: ""
        property string description: ""
        Layout.preferredWidth: 36
        Layout.minimumWidth: Layout.preferredWidth
        Layout.maximumWidth: Layout.preferredWidth
        Layout.preferredHeight: 30
        font: root.controlFont
        leftPadding: 2; rightPadding: 2; topPadding: 2; bottomPadding: 2
        enabled: root.bridge.enabled
        Accessible.name: description
        onActiveFocusChanged: if (activeFocus) actionOwner = root.bridge.ownerToken
        // Passive observation preserves the Button's own pointer/keyboard handling.
        PointHandler { onActiveChanged: if (active) button.actionOwner = root.bridge.ownerToken }
        onClicked: if (command.length) root.bridge.invoke(command, actionOwner)
        PlainTip { visible: button.hovered; text: button.description }
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 4; anchors.rightMargin: 4
        spacing: 2
        NavButton {
            objectName: "analysisNavHome"
            text: "Home"; command: "home"; description: "Reset original view"
            Layout.preferredWidth: 44
        }
        NavButton {
            objectName: "analysisNavBack"
            text: "←"; command: "back"; description: "Back to previous view"
            Layout.preferredWidth: 28
            enabled: root.bridge.backEnabled
        }
        NavButton {
            objectName: "analysisNavForward"
            text: "→"; command: "forward"; description: "Forward to next view"
            Layout.preferredWidth: 28
            enabled: root.bridge.forwardEnabled
        }
        NavButton {
            objectName: "analysisNavPan"
            text: "Pan"; command: "pan"; description: "Pan axes with left mouse; scale with right mouse"
            checked: root.bridge.mode === "pan/zoom"
        }
        NavButton {
            objectName: "analysisNavZoom"
            text: "Zoom"; command: "zoom"; description: "Zoom to rectangle"
            Layout.preferredWidth: 42
            checked: root.bridge.mode === "zoom rect"
        }
        Controls.ComboBox {
            id: displayZoom
            objectName: "analysisNavDisplayZoom"
            property string selectionOwner: ""
            Layout.preferredWidth: 64; Layout.minimumWidth: 64; Layout.maximumWidth: 64
            Layout.preferredHeight: 30
            model: ["Fit", "100%", "150%", "200%"]
            font: root.controlFont
            enabled: root.bridge.enabled
            hoverEnabled: true
            leftPadding: 5; rightPadding: 17
            Accessible.name: "Plot display zoom"
            onActiveFocusChanged: if (activeFocus) selectionOwner = root.bridge.ownerToken
            PointHandler { onActiveChanged: if (active) displayZoom.selectionOwner = root.bridge.ownerToken }
            onActivated: function(index) {
                root.bridge.setZoom(textAt(index), selectionOwner)
                // ComboBox changes currentIndex internally; restore the model projection
                // even when a stale gesture was rejected by the bridge.
                currentIndex = Qt.binding(function() { return displayZoom.model.indexOf(root.bridge.zoomText) })
            }
            currentIndex: model.indexOf(root.bridge.zoomText)
            contentItem: Text {
                text: displayZoom.displayText; textFormat: Text.PlainText
                font: root.controlFont
                color: displayZoom.enabled ? colors.text : colors.disabledText
                verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
            }
            indicator: Text {
                x: displayZoom.width - width - 4; y: (displayZoom.height - height) / 2
                text: "⌄"; font: root.controlFont; color: colors.secondary
            }
            background: Rectangle {
                radius: 4
                color: !displayZoom.enabled ? colors.disabled : displayZoom.down ? colors.pressed : displayZoom.hovered ? colors.hover : colors.surface
                border.width: displayZoom.visualFocus ? 2 : 1
                border.color: displayZoom.visualFocus ? colors.accent : colors.border
            }
            delegate: Controls.ItemDelegate {
                required property string modelData
                required property int index
                objectName: "analysisNavDisplayZoomOption" + index
                width: displayZoom.width
                height: 30
                padding: 4
                highlighted: displayZoom.highlightedIndex === index
                contentItem: Text { text: modelData; textFormat: Text.PlainText; font: root.controlFont; color: colors.text; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { color: parent.highlighted ? colors.selected : colors.surface }
            }
            Component.onCompleted: {
                popup.popupType = Controls.Popup.Window
                popup.y = Qt.binding(function() { return displayZoom.height + 3 })
                popup.height = 124
            }
            PlainTip {
                visible: displayZoom.hovered
                text: "Plot display zoom. Fit keeps labels readable; scroll to see the full plot in smaller windows."
                width: Math.min(320, implicitWidth)
            }
        }
        Text {
            id: message
            objectName: "analysisNavMessage"
            Layout.fillWidth: true; Layout.minimumWidth: 32
            Layout.preferredHeight: 30; Layout.maximumHeight: 30
            text: root.bridge.message
            textFormat: Text.PlainText
            font: root.controlFont; color: colors.secondary
            elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter
            Accessible.name: text
            HoverHandler { id: messageHover }
            PlainTip { objectName: "analysisNavMessageTooltip"; visible: messageHover.hovered && message.text.length > 0; text: message.text; width: Math.min(640, implicitWidth) }
        }
        NavButton {
            id: more
            objectName: "analysisNavMore"
            text: "⋯"; description: "Plot settings and more commands"
            Layout.preferredWidth: 30
            onClicked: if (actionOwner === root.bridge.ownerToken && root.bridge.enabled) {
                menu.actionOwner = actionOwner
                menu.open()
            }
        }
    }

    component MenuCommand: Controls.MenuItem {
        id: entry
        property string command
        property var actionRow: root.action(command)
        property string fallbackText: ""
        text: fallbackText.length ? fallbackText : actionRow.text
        enabled: root.bridge.enabled && (fallbackText.length > 0 || actionRow.enabled)
        font: root.controlFont
        implicitHeight: 32
        onTriggered: root.bridge.invoke(command, menu.actionOwner)
        contentItem: Text { text: entry.text; textFormat: Text.PlainText; font: root.controlFont; color: entry.enabled ? colors.text : colors.disabledText; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { color: entry.highlighted ? colors.selected : colors.surface }
        PlainTip { visible: entry.hovered && entry.actionRow.tooltip.length > 0; text: entry.actionRow.tooltip }
    }

    Controls.Menu {
        id: menu
        objectName: "analysisNavMenu"
        property string actionOwner: ""
        popupType: Controls.Popup.Window
        x: Math.max(0, root.width - width - 4); y: root.height
        width: 238
        padding: 4
        background: Rectangle { color: colors.surface; border.color: colors.border; radius: 4 }
        MenuCommand { objectName: "analysisNavSettingsCopy"; command: "settings_copy" }
        MenuCommand { objectName: "analysisNavSettingsPaste"; command: "settings_paste" }
        MenuCommand { objectName: "analysisNavsettings_paste_to"; command: "settings_paste_to" }
        MenuCommand { objectName: "analysisNavsettings_paste_options"; command: "settings_paste_options" }
        MenuCommand { objectName: "analysisNavsettings_adjust"; command: "settings_adjust" }
        MenuCommand { objectName: "analysisNavsettings_undo_paste"; command: "settings_undo_paste" }
        MenuCommand { objectName: "analysisNavSettingsCopyTo"; command: "settings_copy_to" }
        MenuCommand { objectName: "analysisNavComparison"; command: "comparison" }
        Controls.MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: colors.border } }
        MenuCommand { objectName: "analysisNavSubplots"; command: "subplots"; fallbackText: "Subplots…" }
        MenuCommand { objectName: "analysisNavCustomize"; command: "customize"; fallbackText: "Customize…" }
    }
    Connections {
        target: root.bridge
        function onStateChanged() {
            if (!root.bridge.enabled || menu.actionOwner !== root.bridge.ownerToken) menu.close()
            if (!root.bridge.enabled || displayZoom.selectionOwner !== root.bridge.ownerToken) displayZoom.popup.close()
        }
    }
}
