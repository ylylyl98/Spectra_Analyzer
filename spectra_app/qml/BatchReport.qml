import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts
import QtQuick.Window

Window {
    required property string reportText
    title: "Batch export results"
    width: 760; height: 540
    color: "#ffffff"
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 12
        Controls.ScrollView {
            Layout.fillWidth: true; Layout.fillHeight: true
            Controls.TextArea { text: reportText; readOnly: true; wrapMode: Text.Wrap; selectByMouse: true }
        }
        SurfaceButton { text: "Close"; Layout.alignment: Qt.AlignRight; onClicked: close() }
    }
}
