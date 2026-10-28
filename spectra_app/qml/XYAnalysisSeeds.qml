import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "xy-analysis-seeds"
    required property var bridge
    required property var dialogHost
    property int tab: 0
    property var editors: ({})
    width: 1000; height: 650; color: colors.surface; focus: true
    Accessible.name: "XY seeds and fit results"
    AppColors { id: colors }
    ListModel { id: seedModel }
    function refresh() {
        const rows = bridge.rows
        for (let i = 0; i < rows.length; ++i) {
            let found = -1
            for (let j = i; j < seedModel.count; ++j) if (seedModel.get(j).seedId === rows[i].seedId) { found = j; break }
            if (found < 0) seedModel.insert(i, rows[i])
            else { if (found !== i) seedModel.move(found, i, 1); seedModel.set(i, rows[i]) }
        }
        if (seedModel.count > rows.length) seedModel.remove(rows.length, seedModel.count - rows.length)
    }
    function select(seedId, checked) {
        let ids = bridge.selectedIds.slice()
        const index = ids.indexOf(seedId)
        if (checked && index < 0) ids.push(seedId)
        else if (!checked && index >= 0) ids.splice(index, 1)
        bridge.setSelected(ids)
    }
    function cancel() { bridge.cancel(); dialogHost.reject() }
    Component.onCompleted: refresh()
    Connections {
        target: root.bridge
        function onChanged() { root.refresh() }
        function onRevealRequested(seedId, field) {
            root.tab = 0
            Qt.callLater(function() {
                const editor = root.editors[seedId + "." + field]
                if (!editor) return
                const point = editor.mapToItem(tableBody, 0, 0)
                tableScroll.contentItem.contentY = Math.max(0, Math.min(tableBody.height - tableScroll.availableHeight, point.y - 35))
                tableScroll.contentItem.contentX = Math.max(0, Math.min(tableBody.width - tableScroll.availableWidth, point.x - 25))
                editor.forceActiveFocus(Qt.OtherFocusReason)
                if (editor.selectAll) editor.selectAll()
            })
        }
    }
    component Note: Text { Layout.fillWidth: true; Layout.minimumWidth: 0; font: Qt.application.font; color: colors.secondary; wrapMode: Text.Wrap; textFormat: Text.PlainText }
    component LocalAction: SurfaceButton { font: Qt.application.font; implicitHeight: 36; Layout.minimumWidth: 0 }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 14; spacing: 10
        RowLayout {
            Layout.fillWidth: true
            LocalAction { objectName: "xy-seed-tab"; text: "Seeds"; checkable: true; checked: root.tab === 0; onClicked: root.tab = 0 }
            LocalAction { objectName: "xy-seed-results-tab"; text: "Fit results"; checkable: true; checked: root.tab === 1; onClicked: root.tab = 1 }
            Item { Layout.fillWidth: true }
        }
        Note { visible: root.tab === 0; text: "Select rows to exclude or restore their candidate filter values. Blank fit parameters are estimated; blank lower/upper X bounds are unbounded. Changes are applied together with OK." }
        RowLayout {
            visible: root.tab === 0; Layout.fillWidth: true; spacing: 8
            LocalAction { objectName: "xy-seed-add"; text: "Add"; onClicked: root.bridge.add() }
            LocalAction { objectName: "xy-seed-exclude"; text: "Exclude selected"; enabled: root.bridge.selectedIds.length > 0; onClicked: root.bridge.excludeSelected() }
            LocalAction { objectName: "xy-seed-reset"; text: "Use filter for selected"; enabled: root.bridge.selectedIds.length > 0; onClicked: root.bridge.useFilterSelected() }
        }
        Controls.ScrollView {
            id: tableScroll
            objectName: "xy-seed-table"
            visible: root.tab === 0
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0
            clip: true; contentWidth: tableBody.width; contentHeight: tableBody.height
            Column {
                id: tableBody
                width: tableHeader.implicitWidth; spacing: 6
                Row {
                    id: tableHeader
                    spacing: 6; height: 44
                    Repeater {
                        model: [{label:"Select", size:62}, {label:"Use", size:50}, {label:"Center X", size:108}, {label:"Kind", size:94}, {label:"Height", size:108}, {label:root.bridge.resultModel === "Lorentzian" ? "Width / HWHM" : "Width / sigma", size:108}, {label:"Gamma", size:108}, {label:"Fix X", size:60}, {label:"Lower X", size:108}, {label:"Upper X", size:108}]
                        delegate: Text { required property var modelData; width: modelData.size; height: 44; text: modelData.label; font.family: Qt.application.font.family; font.pointSize: Qt.application.font.pointSize; font.bold: true; color: colors.text; wrapMode: Text.Wrap; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText }
                    }
                }
                Repeater {
                    model: seedModel
                    delegate: Row {
                        id: row
                        required property var model
                        property string seedId: model.seedId
                        spacing: 6; height: 36
                        component Number: XYAnalysisSeedField {
                            bridge: root.bridge; seedId: row.seedId
                            Component.onCompleted: root.editors[seedId + "." + field] = this
                        }
                        SurfaceCheckBox { property string seedId: row.seedId; width: 62; height: 36; text: ""; checked: root.bridge.selectedIds.indexOf(seedId) >= 0; Accessible.name: "Select seed " + seedId; onClicked: root.select(seedId, checked) }
                        SurfaceCheckBox { property string seedId: row.seedId; property string capturedSeed: ""; objectName: "xy-seed-enabled"; width: 50; height: 36; checked: row.model.enabled; Accessible.name: "Use seed " + seedId; onPressed: capturedSeed = seedId; onActiveFocusChanged: if (activeFocus) capturedSeed = seedId; onClicked: root.bridge.edit(capturedSeed, "enabled", checked) }
                        Number { field: "position"; label: "Center X"; value: row.model.position }
                        Controls.ComboBox {
                            id: kindChoice
                            property string seedId: row.seedId
                            property string capturedSeed: ""
                            objectName: "xy-seed-kind"
                            width: 94; height: 36; model: ["peak", "dip"]; font: Qt.application.font
                            Accessible.name: "Kind, seed " + seedId
                            currentIndex: row.model.kind === "dip" ? 1 : 0
                            onActiveFocusChanged: if (activeFocus) capturedSeed = seedId
                            onPressedChanged: if (pressed) capturedSeed = seedId
                            onActivated: root.bridge.edit(capturedSeed, "kind", currentText)
                            Connections { target: kindChoice.popup; function onAboutToShow() { kindChoice.capturedSeed = kindChoice.seedId } }
                            Component.onCompleted: { popup.popupType = Controls.Popup.Window; root.editors[seedId + ".kind"] = this }
                        }
                        Number { field: "amplitude"; label: "Height"; value: row.model.amplitude }
                        Number { field: "width"; label: "Width"; value: row.model.width }
                        Number { field: "gamma"; label: "Gamma"; value: row.model.gamma }
                        SurfaceCheckBox { property string seedId: row.seedId; property string capturedSeed: ""; objectName: "xy-seed-fixedCenter"; width: 60; height: 36; checked: row.model.fixedCenter; Accessible.name: "Fix center X, seed " + seedId; onPressed: capturedSeed = seedId; onActiveFocusChanged: if (activeFocus) capturedSeed = seedId; onClicked: root.bridge.edit(capturedSeed, "fixedCenter", checked) }
                        Number { field: "centerMin"; label: "Lower X"; value: row.model.centerMin }
                        Number { field: "centerMax"; label: "Upper X"; value: row.model.centerMax }
                    }
                }
            }
        }
        Controls.ScrollView {
            visible: root.tab === 1; Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0; contentWidth: availableWidth; clip: true
            ColumnLayout {
                width: parent.width; spacing: 10
                Note { text: "Read-only fit results · " + root.bridge.resultModel; color: colors.text; font.bold: true }
                Note { visible: root.bridge.results.length === 0; text: "No fitted peaks." }
                Repeater {
                    model: root.bridge.results
                    delegate: Note { required property var modelData; objectName: "xy-seed-result"; text: "Center X: " + modelData.center + "    Height: " + modelData.height + "    " + (root.bridge.resultModel === "Lorentzian" ? "HWHM: " : "Sigma: ") + modelData.width + (modelData.gamma ? "    Gamma: " + modelData.gamma : ""); color: colors.text }
                }
            }
        }
        Note { objectName: "xy-seed-error"; visible: !!root.bridge.error; text: root.bridge.error; color: colors.error }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            LocalAction { objectName: "xy-seed-cancel"; text: "Cancel"; onClicked: root.cancel() }
            LocalAction { objectName: "xy-seed-accept"; text: "OK"; primary: true; onClicked: if (root.bridge.accept()) root.dialogHost.accept() }
        }
    }
    Keys.onEscapePressed: root.cancel()
}
