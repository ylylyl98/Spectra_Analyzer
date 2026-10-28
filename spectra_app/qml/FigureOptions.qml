import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Controls.Pane {
    id: root
    objectName: "figureOptions"
    required property var bridge
    required property var dialogHost
    property font controlFont: Qt.application.font
    font: controlFont
    implicitWidth: 620
    implicitHeight: 720
    padding: 12
    AppColors { id: colors }
    background: Rectangle { color: colors.surface }
    Keys.onEscapePressed: event => { dialogHost.reject(); event.accepted = true }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10
        Text {
            objectName: "figurePersistenceHint"
            Layout.fillWidth: true
            text: root.bridge.notice
            font: root.controlFont
            color: colors.secondary
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }
        Controls.TabBar {
            id: tabs
            objectName: "figureTabs"
            Layout.fillWidth: true
            Controls.TabButton { objectName: "figureTabAxes"; text: "Axes"; font: root.controlFont }
            Controls.TabButton { objectName: "figureTabCurves"; text: "Curves (" + root.bridge.curves.length + ")"; font: root.controlFont; enabled: root.bridge.curves.length > 0 }
            Controls.TabButton { objectName: "figureTabMappables"; text: "Colors (" + root.bridge.mappables.length + ")"; font: root.controlFont; enabled: root.bridge.mappables.length > 0; Accessible.name: "Images and collections" }
        }
        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 0
            currentIndex: tabs.currentIndex
            Controls.ScrollView {
                id: axesScroll
                objectName: "figureAxesContent"
                clip: true
                rightPadding: effectiveScrollBarWidth
                contentWidth: availableWidth
                contentHeight: axesForm.implicitHeight
                Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
                ColumnLayout {
                    id: axesForm
                    width: axesScroll.availableWidth
                    spacing: 10
                    FigureOptionsField { bridge: root.bridge; field: "axes.title"; label: "Title"; controlFont: root.controlFont }
                    Repeater {
                        model: root.bridge.axes
                        delegate: ColumnLayout {
                            id: axisForm
                            required property var modelData
                            readonly property string prefix: "axis." + modelData.name + "."
                            Layout.fillWidth: true
                            spacing: 8
                            Text { Layout.fillWidth: true; text: axisForm.modelData.title; font: root.controlFont; color: colors.text; wrapMode: Text.Wrap; textFormat: Text.PlainText }
                            FigureOptionsField { bridge: root.bridge; field: axisForm.prefix + "min"; label: "Minimum"; hint: axisForm.modelData.isDate ? "Date and time (YYYY-MM-DD HH:MM:SS)" : ""; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: axisForm.prefix + "max"; label: "Maximum"; hint: axisForm.modelData.isDate ? "Date and time (YYYY-MM-DD HH:MM:SS)" : ""; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: axisForm.prefix + "label"; label: "Label"; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: axisForm.prefix + "scale"; label: "Scale"; choices: axisForm.modelData.scales; controlFont: root.controlFont }
                        }
                    }
                    SurfaceCheckBox {
                        objectName: "figureLegend"
                        Layout.fillWidth: true
                        text: "Regenerate legend"
                        font: root.controlFont
                        checked: !!root.bridge.drafts["axes.legend"]
                        onClicked: root.bridge.setDraft("axes.legend", checked)
                    }
                }
            }
            Controls.ScrollView {
                id: curvesScroll
                objectName: "figureCurvesContent"
                clip: true
                rightPadding: effectiveScrollBarWidth
                contentWidth: availableWidth
                contentHeight: curvesForm.implicitHeight
                Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
                ColumnLayout {
                    id: curvesForm
                    width: curvesScroll.availableWidth
                    spacing: 10
                    Controls.ComboBox {
                        id: curveSelector
                        objectName: "figureCurveSelector"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        model: root.bridge.curves
                        textRole: "title"
                        font: root.controlFont
                        Accessible.name: "Curve"
                    }
                    Repeater {
                        model: root.bridge.curves
                        delegate: ColumnLayout {
                            id: curveForm
                            required property var modelData
                            required property int index
                            readonly property string prefix: "curve." + modelData.index + "."
                            Layout.fillWidth: true
                            visible: index === curveSelector.currentIndex
                            spacing: 8
                            FigureOptionsField { bridge: root.bridge; field: curveForm.prefix + "label"; label: "Label"; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: curveForm.prefix + "linestyle"; label: "Line style"; choices: curveForm.modelData.lineStyles; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: curveForm.prefix + "drawstyle"; label: "Draw style"; choices: curveForm.modelData.drawStyles; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: curveForm.prefix + "width"; label: "Width"; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: curveForm.prefix + "color"; label: "Line RGBA"; colorField: true; hint: "Color name or #rrggbbaa (alpha last)"; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: curveForm.prefix + "marker"; label: "Marker"; choices: curveForm.modelData.markers; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: curveForm.prefix + "size"; label: "Marker size"; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: curveForm.prefix + "facecolor"; label: "Marker face RGBA"; colorField: true; hint: "Color name or #rrggbbaa (alpha last)"; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: curveForm.prefix + "edgecolor"; label: "Marker edge RGBA"; colorField: true; hint: "Color name or #rrggbbaa (alpha last)"; controlFont: root.controlFont }
                        }
                    }
                }
            }
            Controls.ScrollView {
                id: mappableScroll
                objectName: "figureMappablesContent"
                clip: true
                rightPadding: effectiveScrollBarWidth
                contentWidth: availableWidth
                contentHeight: mappableForm.implicitHeight
                Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
                ColumnLayout {
                    id: mappableForm
                    width: mappableScroll.availableWidth
                    spacing: 10
                    Controls.ComboBox {
                        id: mappableSelector
                        objectName: "figureMappableSelector"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        model: root.bridge.mappables
                        textRole: "title"
                        font: root.controlFont
                        Accessible.name: "Image or collection"
                    }
                    Repeater {
                        model: root.bridge.mappables
                        delegate: ColumnLayout {
                            id: artistForm
                            required property var modelData
                            required property int index
                            readonly property string prefix: "mappable." + modelData.index + "."
                            Layout.fillWidth: true
                            visible: index === mappableSelector.currentIndex
                            spacing: 8
                            FigureOptionsField { bridge: root.bridge; field: artistForm.prefix + "label"; label: "Label"; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: artistForm.prefix + "cmap"; label: "Colormap"; choices: artistForm.modelData.colormaps; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: artistForm.prefix + "min"; label: "Color minimum"; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: artistForm.prefix + "max"; label: "Color maximum"; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: artistForm.prefix + "interpolation"; label: "Interpolation"; choices: artistForm.modelData.interpolations || []; visible: artistForm.modelData.isImage; controlFont: root.controlFont }
                            FigureOptionsField { bridge: root.bridge; field: artistForm.prefix + "stage"; label: "Interpolation stage"; choices: artistForm.modelData.stages || []; visible: artistForm.modelData.isImage; controlFont: root.controlFont }
                        }
                    }
                }
            }
        }
        Text {
            objectName: "figureValidation"
            Layout.fillWidth: true
            text: root.bridge.validationError || Object.values(root.bridge.errors).join("\n")
            visible: text.length > 0
            font: root.controlFont
            color: colors.error
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            SurfaceButton { objectName: "figureApply"; text: "Apply"; font: root.controlFont; enabled: root.bridge.active; onClicked: root.bridge.apply() }
            SurfaceButton { objectName: "figureCancel"; text: "Cancel"; font: root.controlFont; onClicked: root.dialogHost.reject() }
            SurfaceButton { objectName: "figureAccept"; text: "OK"; font: root.controlFont; primary: true; enabled: root.bridge.canAccept; onClicked: { if (root.bridge.apply()) root.dialogHost.accept() } }
        }
    }
}
