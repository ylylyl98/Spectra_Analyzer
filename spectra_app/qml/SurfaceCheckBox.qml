import QtQuick
import QtQuick.Controls.Basic as Controls

Controls.CheckBox {
    id: control

    implicitHeight: 30
    implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding
    padding: 0
    spacing: 7
    font.family: Qt.application.font.family
    font.pixelSize: 12
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus

    AppColors { id: colors }

    indicator: Rectangle {
        implicitWidth: 16
        implicitHeight: 16
        x: control.leftPadding
        y: Math.round((control.height - height) / 2)
        radius: 3
        color: !control.enabled ? colors.disabled
               : control.checked ? (control.down ? colors.accentPressed : colors.accent)
               : control.hovered ? colors.hover : colors.surface
        border.width: control.visualFocus ? 2 : 1
        border.color: control.visualFocus || (control.checked && control.enabled)
                      ? colors.accent : colors.border

        Text {
            anchors.centerIn: parent
            text: "\u2713"
            visible: control.checked
            color: control.enabled ? colors.surface : colors.disabledText
            font.family: Qt.application.font.family
            font.pixelSize: 12
            font.weight: Font.DemiBold
        }
    }

    contentItem: Text {
        text: control.text
        font: control.font
        color: control.enabled ? colors.text : colors.disabledText
        leftPadding: control.indicator.width + control.spacing
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
}
