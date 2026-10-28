import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Controls.Pane {
    id: root
    objectName: "curveColorPrompt"
    required property var bridge
    required property var dialogHost
    property font controlFont: Qt.application.font
    font: controlFont
    implicitWidth: 500
    implicitHeight: 570
    padding: 12

    AppColors { id: colors }
    TextMetrics { id: channelLabelMeasure; font: root.controlFont; text: qsTr("Green") }
    TextMetrics { id: channelValueMeasure; font: root.controlFont; text: "255" }
    background: Rectangle { color: colors.surface }

    onActiveFocusChanged: if (activeFocus) Qt.callLater(function() {
        if (!root || !root.bridge || !root.Window.window || !root.Window.window.visible)
            return
        editor.forceActiveFocus(Qt.OtherFocusReason)
        editor.selectAll()
    })
    Keys.onReturnPressed: event => { root.bridge.accept(); event.accepted = true }
    Keys.onEnterPressed: event => { root.bridge.accept(); event.accepted = true }
    Keys.onEscapePressed: event => { root.bridge.cancel(); event.accepted = true }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Controls.ScrollView {
            id: scroll
            objectName: "curveColorContent"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 0
            clip: true
            rightPadding: effectiveScrollBarWidth
            contentWidth: availableWidth
            contentHeight: form.implicitHeight
            Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff

            ColumnLayout {
                id: form
                width: scroll.availableWidth
                spacing: 8

                Text {
                    Layout.fillWidth: true
                    text: qsTr("Choose a color")
                    font: root.controlFont
                    color: colors.text
                }

                Rectangle {
                    id: saturationValue
                    objectName: "curveColorSaturationValue"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 165
                    color: Qt.hsva(root.bridge.hue, 1, 1, 1)
                    focus: false
                    activeFocusOnTab: true
                    Accessible.name: qsTr("Saturation and brightness; use arrow keys to adjust")
                    Accessible.role: Accessible.Slider

                    Rectangle {
                        anchors.fill: parent
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0; color: "white" }
                            GradientStop { position: 1; color: "transparent" }
                        }
                    }
                    Rectangle {
                        anchors.fill: parent
                        gradient: Gradient {
                            GradientStop { position: 0; color: "transparent" }
                            GradientStop { position: 1; color: "black" }
                        }
                    }
                    Rectangle {
                        x: root.bridge.saturation * (parent.width - 1) - width / 2
                        y: (1 - root.bridge.value) * (parent.height - 1) - height / 2
                        width: 12
                        height: 12
                        radius: 6
                        color: "transparent"
                        border.width: 2
                        border.color: "white"
                        Rectangle { anchors.fill: parent; anchors.margins: 2; radius: 4; color: "transparent"; border.color: "black" }
                    }
                    Rectangle {
                        anchors.fill: parent
                        color: "transparent"
                        border.width: saturationValue.activeFocus ? 2 : 1
                        border.color: saturationValue.activeFocus ? colors.accent : colors.border
                    }
                    function selectAt(x, y) {
                        root.bridge.setHsv(root.bridge.hue,
                                           Math.max(0, Math.min(1, x / Math.max(1, width - 1))),
                                           1 - Math.max(0, Math.min(1, y / Math.max(1, height - 1))))
                    }
                    MouseArea {
                        anchors.fill: parent
                        onPressed: mouse => { saturationValue.forceActiveFocus(Qt.MouseFocusReason); saturationValue.selectAt(mouse.x, mouse.y) }
                        onPositionChanged: mouse => { if (pressed) saturationValue.selectAt(mouse.x, mouse.y) }
                    }
                    Keys.onPressed: event => {
                        let ds = 0, dv = 0
                        if (event.key === Qt.Key_Left) ds = -0.01
                        else if (event.key === Qt.Key_Right) ds = 0.01
                        else if (event.key === Qt.Key_Down) dv = -0.01
                        else if (event.key === Qt.Key_Up) dv = 0.01
                        else { event.accepted = false; return }
                        root.bridge.setHsv(root.bridge.hue,
                                           Math.max(0, Math.min(1, root.bridge.saturation + ds)),
                                           Math.max(0, Math.min(1, root.bridge.value + dv)))
                        event.accepted = true
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Text { text: qsTr("Hue"); color: colors.text; font: root.controlFont; Layout.preferredWidth: Math.max(48, channelLabelMeasure.width) }
                    Controls.Slider {
                        id: hue
                        objectName: "curveColorHue"
                        Layout.fillWidth: true
                        from: 0
                        to: 1
                        stepSize: 0.01
                        value: root.bridge.hue
                        Accessible.name: qsTr("Hue")
                        onMoved: root.bridge.setHsv(value, root.bridge.saturation, root.bridge.value)
                        background: Rectangle {
                            x: hue.leftPadding
                            y: hue.topPadding + (hue.availableHeight - height) / 2
                            width: hue.availableWidth
                            height: 12
                            radius: 3
                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop { position: 0; color: "#ff0000" }
                                GradientStop { position: 1 / 6; color: "#ffff00" }
                                GradientStop { position: 2 / 6; color: "#00ff00" }
                                GradientStop { position: 3 / 6; color: "#00ffff" }
                                GradientStop { position: 4 / 6; color: "#0000ff" }
                                GradientStop { position: 5 / 6; color: "#ff00ff" }
                                GradientStop { position: 1; color: "#ff0000" }
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Rectangle {
                        objectName: "curveColorPreview"
                        Layout.preferredWidth: 54
                        Layout.preferredHeight: 36
                        color: root.bridge.normalizedColor
                        border.color: colors.border
                        radius: 4
                        Accessible.name: qsTr("Current color: ") + root.bridge.normalizedColor
                    }
                    Controls.TextField {
                        id: editor
                        objectName: "curveColorEditor"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: root.bridge.draft
                        font: root.controlFont
                        color: colors.text
                        selectByMouse: true
                        placeholderText: qsTr("Hex or color name")
                        Accessible.name: qsTr("Hex or color name")
                        padding: 8
                        onTextEdited: root.bridge.setDraft(text)
                        Keys.onReturnPressed: event => { root.bridge.accept(); event.accepted = true }
                        Keys.onEnterPressed: event => { root.bridge.accept(); event.accepted = true }
                        background: Rectangle {
                            radius: 4
                            color: colors.surface
                            border.width: editor.activeFocus ? 2 : 1
                            border.color: root.bridge.validationError.length ? colors.error : editor.activeFocus ? colors.accent : colors.border
                        }
                    }
                }

                Repeater {
                    model: ["Red", "Green", "Blue"]
                    delegate: RowLayout {
                        id: channelRow
                        required property int index
                        required property string modelData
                        Layout.fillWidth: true
                        Text { text: qsTr(channelRow.modelData); color: colors.text; font: root.controlFont; Layout.preferredWidth: Math.max(48, channelLabelMeasure.width) }
                        Controls.Slider {
                            objectName: "curveColor" + channelRow.modelData
                            Layout.fillWidth: true
                            from: 0
                            to: 255
                            stepSize: 1
                            value: channelRow.index === 0 ? root.bridge.red : channelRow.index === 1 ? root.bridge.green : root.bridge.blue
                            Accessible.name: channelRow.modelData
                            onMoved: root.bridge.setRgb(channelRow.index === 0 ? Math.round(value) : root.bridge.red,
                                                       channelRow.index === 1 ? Math.round(value) : root.bridge.green,
                                                       channelRow.index === 2 ? Math.round(value) : root.bridge.blue)
                        }
                        Text {
                            Layout.preferredWidth: Math.max(28, channelValueMeasure.width)
                            horizontalAlignment: Text.AlignRight
                            text: channelRow.index === 0 ? root.bridge.red : channelRow.index === 1 ? root.bridge.green : root.bridge.blue
                            color: colors.text
                            font: root.controlFont
                        }
                    }
                }

                Text {
                    objectName: "curveColorValidation"
                    Layout.fillWidth: true
                    visible: text.length > 0
                    text: root.bridge.validationError
                    textFormat: Text.PlainText
                    font: root.controlFont
                    color: colors.error
                    wrapMode: Text.Wrap
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            SurfaceButton {
                objectName: "curveColorAction_cancel"
                text: qsTr("Cancel")
                font: root.controlFont
                onClicked: root.bridge.cancel()
                Keys.onReturnPressed: event => { root.bridge.cancel(); event.accepted = true }
                Keys.onEnterPressed: event => { root.bridge.cancel(); event.accepted = true }
            }
            SurfaceButton {
                objectName: "curveColorAction_ok"
                text: qsTr("OK")
                font: root.controlFont
                primary: true
                enabled: root.bridge.canAccept
                onClicked: root.bridge.accept()
                Keys.onReturnPressed: event => { root.bridge.accept(); event.accepted = true }
                Keys.onEnterPressed: event => { root.bridge.accept(); event.accepted = true }
            }
        }
    }
}
