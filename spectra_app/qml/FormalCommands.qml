import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "formalCommands"
    property font controlFont: Qt.application.font
    required property QtObject bridge
    readonly property var state: bridge.state
    implicitHeight: content.implicitHeight + 10
    color: colors.surface
    AppColors { id: colors }
    function commandEnabled(key) { return (root.bridge.enabled || key === "batch_cancel") && !!root.state.commands && !!root.state.commands[key] && !!root.state.commands[key].enabled }
    function commandTip(key) { return root.state.commands && root.state.commands[key] ? root.state.commands[key].tooltip : "" }
    component Tip: Controls.ToolTip {
        id: tip
        popupType: Controls.Popup.Window
        font: root.controlFont
        delay: 650
        contentItem: Text { text: tip.text; textFormat: Text.PlainText; font: root.controlFont; color: colors.text; wrapMode: Text.Wrap }
    }
    component MenuHoverBackground: Rectangle {
        implicitWidth: 200
        implicitHeight: 40
        color: colors.surface
        border.color: colors.border
        property alias hoverTracker: backgroundHover
        HoverHandler { id: backgroundHover }
    }
    component Command: SurfaceButton {
        id: commandButton
        property string commandKey
        objectName: "formal_" + commandKey
        font: root.controlFont
        implicitHeight: Math.max(32, contentItem.implicitHeight + 12)
        enabled: root.commandEnabled(commandKey)
        onClicked: root.bridge.command(commandKey)
        contentItem: Text { text: commandButton.text; textFormat: Text.PlainText; font: root.controlFont; color: !commandButton.enabled ? colors.disabledText : commandButton.primary ? colors.surface : colors.text; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        Tip { visible: commandButton.hovered && text.length > 0; text: root.commandTip(commandButton.commandKey) }
    }
    component MenuCommand: Controls.MenuItem {
        id: menuCommand
        property string commandKey
        property var hoverTracker
        readonly property bool disabledHovered: {
            if (enabled || !hoverTracker || !hoverTracker.hovered) return false
            const point = mapFromItem(hoverTracker.parent, hoverTracker.point.position.x, hoverTracker.point.position.y)
            return point.x >= 0 && point.x < width && point.y >= 0 && point.y < height
        }
        objectName: "formal_" + commandKey
        font: root.controlFont
        implicitHeight: Math.max(34, contentItem.implicitHeight + 14)
        enabled: root.commandEnabled(commandKey)
        onTriggered: root.bridge.command(commandKey)
        contentItem: Text { text: menuCommand.text; textFormat: Text.PlainText; font: root.controlFont; color: menuCommand.enabled ? colors.text : colors.disabledText; verticalAlignment: Text.AlignVCenter }
        Tip { visible: (menuCommand.hovered || menuCommand.disabledHovered) && text.length > 0; text: root.commandTip(menuCommand.commandKey) }
    }
    component MenuButton: SurfaceButton {
        font: root.controlFont
        implicitHeight: Math.max(32, contentItem.implicitHeight + 12)
        enabled: root.bridge.enabled
    }
    ColumnLayout {
        id: content
        x: 5; y: 5
        width: Math.max(1, root.width - 10)
        spacing: 6
        Flow {
            id: commands
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
            spacing: 5
            Command { commandKey: "analyze"; text: "Analyze"; primary: true }
            Row {
                spacing: 5
                Text { height: zoom.height; text: "Display"; textFormat: Text.PlainText; font: root.controlFont; color: colors.secondary; verticalAlignment: Text.AlignVCenter }
                Controls.ComboBox {
                    id: zoom
                    objectName: "formal_zoom"
                    width: Math.max(84, contentItem.implicitWidth + 32)
                    implicitHeight: Math.max(32, contentItem.implicitHeight + 12)
                    font: root.controlFont
                    enabled: root.bridge.enabled
                    model: ["Fit", "100%", "150%", "200%"]
                    Accessible.name: "Display zoom"
                    onActivated: root.bridge.setZoom(currentText)
                    Component.onCompleted: {
                        popup.popupType = Controls.Popup.Window
                        popup.height = Qt.binding(function() {
                            return Math.min(popup.contentItem.implicitHeight, zoom.Screen.desktopAvailableHeight - popup.topMargin - popup.bottomMargin)
                        })
                    }
                    Binding on currentIndex { value: Math.max(0, zoom.model.indexOf(root.state.zoom || "Fit")) }
                    contentItem: Text { text: zoom.displayText; textFormat: Text.PlainText; font: root.controlFont; color: zoom.enabled ? colors.text : colors.disabledText; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                    delegate: Controls.ItemDelegate { required property string modelData; required property int index; width: zoom.width; highlighted: zoom.highlightedIndex === index; font: root.controlFont; contentItem: Text { text: modelData; textFormat: Text.PlainText; font: root.controlFont; color: colors.text; verticalAlignment: Text.AlignVCenter } }
                }
            }
            Command { commandKey: "copy_svg"; text: "Copy SVG" }
            Command { commandKey: "export_svg"; text: "Export SVG…" }
            Command { commandKey: "export_png"; text: "Export PNG…" }
            MenuButton {
                id: settingsButton
                objectName: "formal_settings_menu"
                text: "Settings ▾"
                onClicked: settingsMenu.open()
                Controls.Menu {
                    id: settingsMenu
                    parent: settingsButton
                    x: 0
                    y: settingsButton.height
                    popupType: Controls.Popup.Window
                    font: root.controlFont
                    background: MenuHoverBackground { id: settingsHoverBackground }
                    MenuCommand { commandKey: "settings_copy"; text: "Copy settings"; hoverTracker: settingsHoverBackground.hoverTracker }
                    MenuCommand { commandKey: "settings_paste"; text: "Paste settings"; hoverTracker: settingsHoverBackground.hoverTracker }
                    MenuCommand { commandKey: "settings_copy_to"; text: "Copy to active graphs…"; hoverTracker: settingsHoverBackground.hoverTracker }
                    Controls.MenuSeparator {}
                    MenuCommand { commandKey: "comparison"; text: "Comparison snapshot…"; hoverTracker: settingsHoverBackground.hoverTracker }
                }
            }
            MenuButton {
                id: formatsButton
                objectName: "formal_formats_menu"
                text: "Formats ▾"
                onClicked: formatsMenu.open()
                Controls.Menu {
                    id: formatsMenu
                    parent: formatsButton
                    x: 0
                    y: formatsButton.height
                    popupType: Controls.Popup.Window
                    font: root.controlFont
                    background: MenuHoverBackground { id: formatsHoverBackground }
                    MenuCommand { commandKey: "format_save"; text: "Save format…"; hoverTracker: formatsHoverBackground.hoverTracker }
                    MenuCommand { commandKey: "format_load"; text: "Load selected format"; hoverTracker: formatsHoverBackground.hoverTracker }
                    MenuCommand { commandKey: "format_delete"; text: "Delete selected format"; hoverTracker: formatsHoverBackground.hoverTracker }
                    Controls.MenuSeparator {}
                    MenuCommand { commandKey: "format_apply"; text: "Apply format to graphs…"; hoverTracker: formatsHoverBackground.hoverTracker }
                }
            }
            MenuButton {
                id: batchButton
                objectName: "formal_batch_menu"
                text: "Batch ▾"
                onClicked: batchMenu.open()
                Controls.Menu {
                    id: batchMenu
                    parent: batchButton
                    x: 0
                    y: batchButton.height
                    popupType: Controls.Popup.Window
                    font: root.controlFont
                    background: MenuHoverBackground { id: batchHoverBackground }
                    MenuCommand { commandKey: "batch_export"; text: "Batch export…"; hoverTracker: batchHoverBackground.hoverTracker }
                    MenuCommand { commandKey: "batch_results"; text: "Batch results…"; hoverTracker: batchHoverBackground.hoverTracker }
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            spacing: 5
            visible: !!root.state.batch && root.state.batch.visible
            RowLayout {
                Layout.fillWidth: true
                Text {
                    objectName: "formal_batch_status"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: root.state.batch ? root.state.batch.status : ""
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    font: root.controlFont
                    color: colors.secondary
                    Tip { visible: hovered.hovered && text.length > 0; text: root.state.batch ? root.state.batch.tooltip : "" }
                    HoverHandler { id: hovered }
                }
                Command { commandKey: "batch_cancel"; text: "Cancel"; visible: !!root.state.batch && root.state.batch.cancelVisible }
            }
            Controls.ProgressBar {
                objectName: "formal_batch_progress"
                Layout.fillWidth: true
                from: 0
                to: root.state.batch ? Math.max(1, root.state.batch.maximum) : 1
                value: root.state.batch ? root.state.batch.value : 0
                indeterminate: !!root.state.batch && root.state.batch.maximum === 0
            }
        }
    }
}
