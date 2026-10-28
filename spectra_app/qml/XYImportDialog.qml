import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Pane {
    id: root
    required property var bridge
    required property var dialogHost
    padding: 0
    font: bridge.applicationFont
    background: Rectangle { color: palette.window }
    SystemPalette { id: palette }
    implicitWidth: 920
    implicitHeight: 620
    activeFocusOnTab: true
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 5
        Label { textFormat: Text.PlainText;
            Layout.fillWidth: true
            text: "Review types, column roles and destinations. All rows and columns are retained."
            wrapMode: Text.WordWrap
        }
        RowLayout {
            Layout.fillWidth: true
            Label { textFormat: Text.PlainText; id: destinationLabel; text: "Destination:"; Layout.preferredWidth: Math.max(implicitWidth, comparisonLabel.implicitWidth) }
            XYImportChoice {
                objectName: "xyImportDestination"
                Layout.fillWidth: true
                Layout.minimumWidth: 120
                model: bridge.destinations
                currentIndex: bridge.destinationIndex
                onActivated: index => bridge.setDestinationIndex(index)
                Accessible.name: "Destination workbook"
            }
            TextField {
                objectName: "xyImportWorkbookName"
                Layout.fillWidth: true
                Layout.minimumWidth: 110
                visible: bridge.newWorkbook
                text: bridge.workbookName
                onTextEdited: bridge.setWorkbookName(text)
                Accessible.name: "New workbook name"
                selectByMouse: true
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Label { textFormat: Text.PlainText; id: comparisonLabel; text: "Comparison:"; Layout.preferredWidth: Math.max(implicitWidth, destinationLabel.implicitWidth) }
            XYImportChoice {
                objectName: "xyImportComparison"
                Layout.fillWidth: true
                visible: !bridge.fixedComparison
                model: bridge.comparisonNames
                currentIndex: bridge.comparisonIndex
                onActivated: index => bridge.setComparisonIndex(index)
                Accessible.name: "Destination comparison"
            }
            Label { textFormat: Text.PlainText;
                objectName: "xyImportFixedComparison"
                visible: bridge.fixedComparison
                Layout.fillWidth: true
                text: bridge.comparisonNames[0]
                elide: Text.ElideRight
                ToolTip.visible: fixedHover.hovered
                ToolTip.text: text
                HoverHandler { id: fixedHover }
            }
        }
        ScrollView {
            id: editorScroll
            objectName: "xyImportEditorScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            contentHeight: Math.max(availableHeight, editor.implicitHeight)
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            RowLayout {
                id: editor
                width: editorScroll.availableWidth
                height: Math.max(editorScroll.availableHeight, implicitHeight)
                spacing: 8
                ColumnLayout {
                    Layout.preferredWidth: Math.max(128, Math.min(210, editor.width * 0.28))
                    Layout.fillHeight: true
                    ListView {
                        id: files
                        objectName: "xyImportSources"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 160
                        clip: true
                        model: bridge.sources
                        currentIndex: bridge.sourceIndex
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {}
                        keyNavigationEnabled: false
                        Keys.onDownPressed: bridge.selectSource(Math.min(count - 1, currentIndex + 1))
                        Keys.onUpPressed: bridge.selectSource(Math.max(0, currentIndex - 1))
                        delegate: ItemDelegate {
                            required property int index
                            required property string caption
                            required property string summary
                            required property string sourceTooltip
                            objectName: "xyImportSource_" + index
                            width: files.width
                            height: Math.max(52, sourceCaption.implicitHeight + sourceSummary.implicitHeight + 14)
                            highlighted: bridge.sourceIndex === index
                            onClicked: { files.forceActiveFocus(); bridge.selectSource(index) }
                            Accessible.name: caption + ". " + summary
                            contentItem: Column {
                                spacing: 3
                                Label { textFormat: Text.PlainText; id: sourceCaption; width: parent.width; text: caption; elide: Text.ElideRight }
                                Label { textFormat: Text.PlainText; id: sourceSummary; width: parent.width; text: summary; elide: Text.ElideRight; opacity: 0.75 }
                            }
                            ToolTip.visible: hovered
                            ToolTip.text: sourceTooltip + "\n" + summary
                        }
                    }
                    Button {
                        id: applyMatching
                        objectName: "xyImportApplyMatching"
                        Layout.fillWidth: true
                        text: "Apply to matching files"
                        contentItem: Label { textFormat: Text.PlainText; text: applyMatching.text; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter }
                        enabled: bridge.canImport
                        onClicked: bridge.applyMatching()
                        ToolTip.visible: hovered
                        ToolTip.text: "Unique exact names may move. Headerless files require the same column count and generated order."
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 4
                    RowLayout {
                        Layout.fillWidth: true
                        visible: bridge.hasTypes
                        Label { textFormat: Text.PlainText; text: "Data type:" }
                        XYImportChoice {
                            objectName: "xyImportType"
                            Layout.fillWidth: true
                            model: bridge.typeChoices
                            textRole: "label"
                            currentIndex: bridge.kind === "dat" ? 1 : 0
                            enabled: bridge.typeEditable
                            onActivated: index => bridge.setKind(model[index].kind)
                            Accessible.name: "Data type for selected file"
                            ToolTip.visible: hovered && bridge.typeChoices.some(option => !option.available)
                            ToolTip.text: bridge.typeChoices.filter(option => !option.available).map(option => option.label + ": " + option.error).join("\n")
                            delegate: ItemDelegate {
                                required property var modelData
                                width: parent ? parent.width : 160
                                text: modelData.label + (modelData.available ? "" : " (unavailable)")
                                enabled: modelData.available
                                ToolTip.visible: hovered && modelData.error.length > 0
                                ToolTip.text: modelData.error
                            }
                        }
                    }
                    Label { textFormat: Text.PlainText;
                        objectName: "xyImportTypeReason"
                        Layout.fillWidth: true
                        visible: bridge.hasTypes
                        text: bridge.typeReason
                        wrapMode: Text.WordWrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        ToolTip.visible: reasonHover.hovered
                        ToolTip.text: text
                        HoverHandler { id: reasonHover }
                    }
                    Label { textFormat: Text.PlainText;
                        objectName: "xyImportPreviewInfo"
                        Layout.fillWidth: true
                        text: bridge.previewInfo
                        elide: Text.ElideRight
                        ToolTip.visible: infoHover.hovered
                        ToolTip.text: text
                        HoverHandler { id: infoHover }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 116
                        color: palette.base
                        border.color: palette.mid
                        HorizontalHeaderView {
                            id: header
                            objectName: "xyImportPreviewHeaders"
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            syncView: preview
                            clip: true
                            height: 25
                            delegate: Rectangle {
                                required property string display
                                implicitWidth: 132
                                implicitHeight: 25
                                color: palette.button
                                border.color: palette.mid
                                Label { textFormat: Text.PlainText; anchors.fill: parent; anchors.margins: 4; text: display; elide: Text.ElideRight }
                            }
                        }
                        TableView {
                            id: preview
                            objectName: "xyImportPreview"
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: header.bottom
                            anchors.bottom: parent.bottom
                            anchors.margins: 1
                            model: bridge.preview
                            clip: true
                            columnWidthProvider: column => 132
                            rowHeightProvider: row => 25
                            boundsBehavior: Flickable.StopAtBounds
                            ScrollBar.horizontal: ScrollBar {}
                            ScrollBar.vertical: ScrollBar {}
                            delegate: Rectangle {
                                required property string display
                                required property int row
                                implicitWidth: 132
                                implicitHeight: 25
                                color: row % 2 ? palette.alternateBase : palette.base
                                Label { textFormat: Text.PlainText; anchors.fill: parent; anchors.margins: 4; text: display; horizontalAlignment: Text.AlignRight; elide: Text.ElideRight }
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { textFormat: Text.PlainText; id: xLabel; text: "X column:"; Layout.preferredWidth: Math.max(implicitWidth, yLabel.implicitWidth) }
                        XYImportChoice {
                            objectName: "xyImportX"
                            Layout.fillWidth: true
                            model: bridge.columnNames
                            currentIndex: bridge.xColumn
                            enabled: bridge.xEditable
                            onActivated: index => bridge.setX(index)
                            Accessible.name: "X column for selected file"
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { textFormat: Text.PlainText; id: yLabel; text: "Y columns:"; Layout.preferredWidth: Math.max(implicitWidth, xLabel.implicitWidth) }
                        ListView {
                            id: ys
                            objectName: "xyImportYColumns"
                            Layout.fillWidth: true
                            Layout.preferredHeight: 36
                            orientation: ListView.Horizontal
                            spacing: 6
                            clip: true
                            model: bridge.columns
                            ScrollBar.horizontal: ScrollBar {}
                            delegate: CheckBox {
                                id: yOption
                                required property int columnIndex
                                required property string caption
                                required property bool selectedY
                                required property bool allowedY
                                objectName: "xyImportY_" + columnIndex
                                text: caption
                                checked: selectedY
                                enabled: allowedY
                                onToggled: bridge.setY(columnIndex, checked)
                                Accessible.name: "Y column " + caption
                                contentItem: Label {
                                    text: yOption.text
                                    font: yOption.font
                                    textFormat: Text.PlainText
                                    leftPadding: yOption.indicator.width + yOption.spacing
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }
                        }
                    }
                }
            }
        }
        Label { textFormat: Text.PlainText;
            objectName: "xyImportFeedback"
            Layout.fillWidth: true
            text: bridge.feedback
            wrapMode: Text.WordWrap
            maximumLineCount: 2
            elide: Text.ElideRight
            ToolTip.visible: feedbackHover.hovered
            ToolTip.text: text
            HoverHandler { id: feedbackHover }
        }
        Label { textFormat: Text.PlainText;
            objectName: "xyImportValidation"
            Layout.fillWidth: true
            visible: text.length > 0
            text: bridge.validation
            color: "#b33b28"
            wrapMode: Text.WordWrap
            maximumLineCount: 2
            elide: Text.ElideRight
        }
        CheckBox {
            objectName: "xyImportIncludeExisting"
            text: "Include existing curves (add again)"
            checked: bridge.includeExisting
            onToggled: bridge.setIncludeExisting(checked)
            ToolTip.visible: hovered
            ToolTip.text: "Unchecked: skip curves with the same Sheet, X, Y and values already in the comparison."
        }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Button { objectName: "xyImportCancel"; text: "Cancel"; onClicked: dialogHost.reject() }
            Button {
                objectName: "xyImportOnly"
                text: "Import only"
                visible: !bridge.fixedComparison
                enabled: bridge.canImport
                onClicked: bridge.commit(false)
            }
            Button {
                objectName: "xyImportAndCompare"
                text: bridge.compareText
                enabled: bridge.canCompare
                highlighted: bridge.fixedComparison
                onClicked: bridge.commit(true)
            }
        }
    }
    Keys.onReturnPressed: event => { bridge.commit(bridge.fixedComparison); event.accepted = true }
}
