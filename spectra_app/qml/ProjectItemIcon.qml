import QtQuick

Item {
    id: icon
    required property string kind
    required property bool hasChildren
    required property bool expanded
    required property color iconColor
    required property color surfaceColor
    readonly property bool folder: hasChildren || kind === "workbook" || kind === "group" || kind === "source"

    Item {
        anchors.fill: parent
        visible: icon.folder
        Rectangle {
            x: 1
            y: 2
            width: 7
            height: 4
            radius: 1
            color: icon.iconColor
        }
        Rectangle {
            x: 1
            y: 5
            width: 14
            height: 10
            radius: 1.5
            color: "transparent"
            border.width: 1.2
            border.color: icon.iconColor
        }
        Rectangle {
            x: 3
            y: 8
            width: 11
            height: 1
            color: icon.iconColor
            visible: icon.expanded
        }
    }

    Item {
        anchors.fill: parent
        visible: !icon.folder && icon.kind !== "heatmap" && icon.kind !== "xy"
        Rectangle {
            x: 2
            y: 1
            width: 12
            height: 14
            radius: 1
            color: "transparent"
            border.width: 1.2
            border.color: icon.iconColor
        }
        Rectangle { x: 3; y: 5; width: 10; height: 1; color: icon.iconColor }
        Rectangle { x: 3; y: 9; width: 10; height: 1; color: icon.iconColor }
        Rectangle { x: 7; y: 2; width: 1; height: 12; color: icon.iconColor }
    }

    Item {
        anchors.fill: parent
        visible: !icon.folder && icon.kind === "heatmap"
        Rectangle {
            anchors.fill: parent
            anchors.margins: 1
            radius: 1
            color: "transparent"
            border.width: 1
            border.color: icon.iconColor
        }
        Repeater {
            model: 9
            Rectangle {
                required property int index
                x: 3 + (index % 3) * 3.5
                y: 3 + Math.floor(index / 3) * 3.5
                width: 2.5
                height: 2.5
                color: icon.iconColor
                opacity: [0.3, 0.5, 0.9, 0.5, 1, 0.65, 0.95, 0.65, 0.3][index]
            }
        }
    }

    Item {
        anchors.fill: parent
        visible: !icon.folder && icon.kind === "xy"
        Rectangle { x: 1; y: 1; width: 1; height: 14; color: icon.iconColor }
        Rectangle { x: 1; y: 14; width: 14; height: 1; color: icon.iconColor }
        Rectangle { x: 3; y: 10; width: 5; height: 1.3; rotation: -37; transformOrigin: Item.Left; color: icon.iconColor }
        Rectangle { x: 7; y: 7; width: 4; height: 1.3; rotation: 28; transformOrigin: Item.Left; color: icon.iconColor }
        Rectangle { x: 10; y: 9; width: 6; height: 1.3; rotation: -58; transformOrigin: Item.Left; color: icon.iconColor }
    }
}
