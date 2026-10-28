import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    required property var bridge
    required property var dialogHost
    readonly property bool wide: width >= 650
    color: colors.surface
    width: 960
    height: 680
    focus: true
    AppColors { id: colors }
    Keys.onReturnPressed: dialogHost.accept()
    component NarrowTab: Controls.TabButton {
        id: tab
        implicitHeight: Math.max(40, contentItem.implicitHeight + 12)
        font: bridge.applicationFont
        contentItem: Text {
            text: tab.text
            textFormat: Text.PlainText
            font: tab.font
            color: tab.checked ? colors.accent : colors.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            color: tab.down ? colors.pressed : tab.checked ? colors.selected
                   : tab.hovered ? colors.hover : colors.surface
            border.color: tab.checked || tab.visualFocus ? colors.accent : colors.border
            border.width: tab.visualFocus ? 2 : 1
            radius: 3
        }
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 7
        Controls.Label {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            text: "Check Sheets, then review X and Y columns. A Workbook checkbox selects all its Sheets."
            wrapMode: Text.Wrap
            font: bridge.applicationFont
            color: colors.text
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Controls.Label { text: "Destination:"; font: bridge.applicationFont; color: colors.text }
            Controls.ComboBox {
                objectName: "sheetDestination"
                visible: !bridge.fixedDestination
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                font: bridge.applicationFont
                model: bridge.destinations
                textRole: "label"
                currentIndex: bridge.destinationIndex
                onActivated: bridge.setDestination(currentIndex)
                Accessible.name: "Destination comparison"
            }
            Controls.Label {
                visible: bridge.fixedDestination
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                text: bridge.destinationLabel
                elide: Text.ElideRight
                textFormat: Text.PlainText
                font: bridge.applicationFont
                color: colors.text
            }
        }
        Controls.TabBar {
            id: tabs
            objectName: "sheetNarrowTabs"
            visible: !root.wide
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            NarrowTab { objectName: "sheetSourcesTab"; text: "Sources"; font: bridge.applicationFont }
            NarrowTab { objectName: "sheetColumnsTab"; text: "Columns and preview"; font: bridge.applicationFont }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.fillHeight: true
            spacing: 12
            SheetComparisonSources {
                bridge: root.bridge
                visible: root.wide || tabs.currentIndex === 0
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                Layout.fillHeight: true
                Layout.preferredWidth: root.wide ? 245 : 700
            }
            SheetComparisonEditor {
                bridge: root.bridge
                visible: root.wide || tabs.currentIndex === 1
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                Layout.fillHeight: true
                Layout.preferredWidth: 650
            }
        }
        Controls.Label {
            objectName: "sheetSummary"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            text: bridge.summary
            wrapMode: Text.Wrap
            font: bridge.applicationFont
            color: colors.text
        }
        Controls.Label {
            objectName: "sheetFeedback"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            visible: text.length > 0
            text: bridge.feedback
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
            font: bridge.applicationFont
            color: bridge.canAccept ? colors.secondary : colors.error
            textFormat: Text.PlainText
            Accessible.name: text
            Controls.ToolTip {
                visible: feedbackHover.containsMouse
                text: bridge.feedback
                contentItem: Text {
                    text: bridge.feedback
                    textFormat: Text.PlainText
                    font: bridge.applicationFont
                    color: colors.text
                }
            }
            MouseArea { id: feedbackHover; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
        }
        Controls.CheckBox {
            id: repeatOption
            objectName: "sheetIncludeExisting"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            text: "Include existing curves (add again)"
            font: bridge.applicationFont
            contentItem: Text {
                text: repeatOption.text
                textFormat: Text.PlainText
                font: bridge.applicationFont
                color: colors.text
                leftPadding: repeatOption.indicator.width + repeatOption.spacing
                verticalAlignment: Text.AlignVCenter
                wrapMode: Text.Wrap
            }
            checked: bridge.includeExisting
            onClicked: bridge.setIncludeExisting(checked)
            Accessible.name: text
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Item { Layout.fillWidth: true
            Layout.minimumWidth: 0 }
            Controls.Button {
                objectName: "sheetCancel"
                text: "Cancel"
                font: bridge.applicationFont
                onClicked: dialogHost.reject()
            }
            Controls.Button {
                objectName: "sheetAccept"
                text: bridge.actionText
                font: bridge.applicationFont
                enabled: bridge.canAccept
                highlighted: true
                onClicked: dialogHost.accept()
                Accessible.name: text
            }
        }
    }
}
