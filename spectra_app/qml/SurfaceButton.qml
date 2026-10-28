import QtQuick
import QtQuick.Controls.Basic as Controls

Controls.Button {
    id: control

    property bool primary: false
    property bool quiet: false

    implicitHeight: 32
    implicitWidth: Math.max(32, contentItem.implicitWidth + leftPadding + rightPadding)
    leftPadding: 12
    rightPadding: 12
    topPadding: 5
    bottomPadding: 5
    font.family: Qt.application.font.family
    font.pixelSize: 12
    font.weight: primary ? Font.DemiBold : Font.Normal
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus

    AppColors { id: colors }

    contentItem: Text {
        text: control.text
        font: control.font
        color: !control.enabled ? colors.disabledText
               : control.primary ? colors.surface
               : control.checked ? colors.accent : colors.text
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        radius: 4
        color: !control.enabled ? (control.quiet ? "transparent" : colors.disabled)
               : control.primary ? (control.down ? colors.accentPressed
                                                   : control.hovered ? colors.accentHover : colors.accent)
               : control.down ? colors.pressed
               : control.checked ? colors.selected
               : control.hovered ? colors.hover
               : control.quiet ? "transparent" : colors.surface
        border.width: control.visualFocus ? 2 : 1
        border.color: control.visualFocus ? colors.accent
                      : control.checked ? colors.accent
                      : control.primary || control.quiet ? "transparent" : colors.border
    }
}
