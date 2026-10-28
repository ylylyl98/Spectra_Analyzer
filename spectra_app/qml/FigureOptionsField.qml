import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

ColumnLayout {
    id: root
    required property var bridge
    required property string field
    required property string label
    property string hint: ""
    property bool colorField: false
    property var choices: null
    property font controlFont: Qt.application.font
    readonly property string error: String(bridge.errors[field] || "")
    readonly property var rgba: {
        let revision = bridge.drafts
        return colorField ? bridge.colorPreview(field) : []
    }
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    spacing: 3
    AppColors { id: colors }
    Text {
        Layout.fillWidth: true
        text: root.label
        font: root.controlFont
        color: root.enabled ? colors.text : colors.disabledText
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }
    RowLayout {
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        Rectangle {
            objectName: "figureColor_" + root.field
            visible: root.colorField
            Layout.preferredWidth: 36
            Layout.preferredHeight: 34
            color: colors.surface
            border.color: colors.border
            // Numeric RGBA avoids Qt's #aarrggbb interpretation of Matplotlib hex.
            Rectangle {
                anchors.fill: parent
                anchors.margins: 3
                color: root.rgba.length === 4 ? Qt.rgba(root.rgba[0], root.rgba[1], root.rgba[2], root.rgba[3]) : "transparent"
                objectName: "figureColorFill_" + root.field
            }
            Accessible.name: root.label + " preview"
        }
        Controls.TextField {
            id: editor
            objectName: "figureField_" + root.field
            visible: root.choices === null
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            implicitHeight: Math.max(34, contentHeight + topPadding + bottomPadding)
            text: String(root.bridge.drafts[root.field] === undefined ? "" : root.bridge.drafts[root.field])
            font: root.controlFont
            color: enabled ? colors.text : colors.disabledText
            padding: 8
            selectByMouse: true
            Accessible.name: root.label
            Accessible.description: root.error || root.hint
            onTextEdited: root.bridge.setDraft(root.field, text)
            background: Rectangle {
                radius: 4
                color: !editor.enabled ? colors.disabled : root.error ? colors.errorSurface : colors.surface
                border.width: editor.activeFocus ? 2 : 1
                border.color: root.error ? colors.error : editor.activeFocus ? colors.accent : colors.border
            }
        }
        Controls.ComboBox {
            id: choice
            objectName: "figureChoice_" + root.field
            visible: root.choices !== null
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            implicitHeight: Math.max(34, contentItem.implicitHeight + 14)
            model: root.choices || []
            textRole: "label"
            valueRole: "value"
            font: root.controlFont
            Accessible.name: root.label
            Accessible.description: root.error
            onActivated: root.bridge.setDraft(root.field, currentValue)
            Binding on currentIndex {
                value: {
                    const values = root.choices || []
                    for (let i = 0; i < values.length; ++i)
                        if (String(values[i].value) === String(root.bridge.drafts[root.field])) return i
                    return -1
                }
            }
            contentItem: Text {
                text: choice.displayText
                font: root.controlFont
                color: choice.enabled ? colors.text : colors.disabledText
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
                textFormat: Text.PlainText
                leftPadding: 8
                rightPadding: 24
            }
            background: Rectangle {
                radius: 4
                color: !choice.enabled ? colors.disabled : choice.down ? colors.pressed : choice.hovered ? colors.hover : colors.surface
                border.width: choice.visualFocus ? 2 : 1
                border.color: root.error ? colors.error : choice.visualFocus ? colors.accent : colors.border
            }
        }
    }
    Text {
        Layout.fillWidth: true
        visible: root.error.length > 0 || root.hint.length > 0
        text: root.error || root.hint
        font: root.controlFont
        color: root.error ? colors.error : colors.secondary
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
        objectName: "figureError_" + root.field
    }
}
