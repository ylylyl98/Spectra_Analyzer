import QtQuick
import QtQuick.Controls

ComboBox {
    id: choice
    contentItem: Label {
        text: choice.displayText
        font: choice.font
        textFormat: Text.PlainText
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    delegate: ItemDelegate {
        id: option
        required property int index
        required property var modelData
        width: choice.width
        text: String(modelData)
        highlighted: choice.highlightedIndex === index
        contentItem: Label {
            text: option.text
            font: option.font
            textFormat: Text.PlainText
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }
}
