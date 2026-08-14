import QtQuick 2.7
import QtQuick.Controls 2.1
import QtQuick.Layouts 1.3
import QtQuick.Window 2.2

import UM 1.3 as UM

Window {
    id: root

    property string uiStatus: "UI loaded."

    width: 900
    height: 700
    minimumWidth: 760
    minimumHeight: 560
    modality: Qt.NonModal
    color: UM.Theme.getColor("main_background")
    title: "Eventide Shared Profiles"

    function populateCapabilityFields() {
        maxFlow.text = eventideBridge.capabilityMaxVolumetricFlow
        maxSpeed.text = eventideBridge.capabilityMaxLinearSpeed
        pressureAdvance.text = eventideBridge.capabilityPressureAdvance
        tempOffset.text = eventideBridge.capabilityTemperatureOffset
        retractDistance.text = eventideBridge.capabilityRetractionDistance
        retractSpeed.text = eventideBridge.capabilityRetractionSpeed
        nozzleDiameter.text = eventideBridge.capabilityNozzleDiameter
        nozzleMaterial.text = eventideBridge.capabilityNozzleMaterial
        klipperPa.checked = eventideBridge.capabilityEmitKlipperPA
        capabilityNotes.text = eventideBridge.capabilityNotes
    }

    function loadCapability() {
        uiStatus = eventideBridge.loadCurrentCapability(libraryPath.text)
        if (eventideBridge.capabilityLoaded) {
            populateCapabilityFields()
        }
    }

    function saveCapability() {
        var payload = {
            "expected_revision": eventideBridge.capabilityRevision,
            "max_volumetric_flow_mm3_s": maxFlow.text,
            "max_linear_speed_mm_s": maxSpeed.text,
            "pressure_advance": pressureAdvance.text,
            "emit_klipper_pressure_advance": klipperPa.checked,
            "temperature_offset_c": tempOffset.text,
            "retraction_distance_mm": retractDistance.text,
            "retraction_speed_mm_s": retractSpeed.text,
            "nozzle_diameter_mm": nozzleDiameter.text,
            "nozzle_material": nozzleMaterial.text,
            "notes": capabilityNotes.text
        }

        uiStatus = eventideBridge.saveCurrentCapability(
            libraryPath.text,
            JSON.stringify(payload)
        )

        if (uiStatus.indexOf("CAPABILITY SAVED:") === 0) {
            populateCapabilityFields()
        }
    }

    Component.onCompleted: {
        libraryPath.text = eventideBridge.sharedLibraryPath
        uiStatus = eventideBridge.ping()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 10

        RowLayout {
            Layout.fillWidth: true

            ColumnLayout {
                spacing: 2

                Label {
                    text: "Eventide Shared Profiles"
                    font.pixelSize: 22
                    font.bold: true
                    color: UM.Theme.getColor("text")
                }

                Label {
                    text: "v0.6.2 — transient slice integration"
                    color: UM.Theme.getColor("text")
                    opacity: 0.7
                }
            }

            Item { Layout.fillWidth: true }

            Label {
                text: eventideBridge.currentRegistration
                color: UM.Theme.getColor("text")
                opacity: 0.8
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: statusLabel.implicitHeight + 16
            color: UM.Theme.getColor("main_background")
            border.width: 1
            border.color: UM.Theme.getColor("lining")

            Label {
                id: statusLabel
                anchors.fill: parent
                anchors.margins: 8
                text: root.uiStatus
                color: UM.Theme.getColor("text")
                wrapMode: Text.WordWrap
            }
        }

        TabBar {
            id: tabs
            Layout.fillWidth: true

            TabButton { text: "Library" }
            TabButton { text: "Current Selection" }
            TabButton { text: "Capability" }
        }

        StackLayout {
            id: pages
            currentIndex: tabs.currentIndex
            Layout.fillWidth: true
            Layout.fillHeight: true

            // ----------------------------------------------------------
            // Library tab
            // ----------------------------------------------------------
            Item {
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 12

                    GroupBox {
                        title: "Shared Profile Library"
                        Layout.fillWidth: true

                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 10

                            TextField {
                                id: libraryPath
                                Layout.fillWidth: true
                                selectByMouse: true
                                placeholderText: "\\\\server\\share\\CuraProfiles"
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8

                                Button {
                                    text: "Save Path"
                                    onClicked: uiStatus = eventideBridge.saveSharedLibraryPath(libraryPath.text)
                                }
                                Button {
                                    text: "Test Connection"
                                    onClicked: uiStatus = eventideBridge.testConnection(libraryPath.text)
                                }
                                Button {
                                    text: "Initialize Library"
                                    onClicked: uiStatus = eventideBridge.initializeLibrary(libraryPath.text)
                                }
                                Button {
                                    text: "Refresh Library"
                                    onClicked: uiStatus = eventideBridge.refreshLibrary(libraryPath.text)
                                }
                                Item { Layout.fillWidth: true }
                            }
                        }
                    }

                    GroupBox {
                        title: "Shared Library Inventory"
                        Layout.fillWidth: true

                        GridLayout {
                            anchors.fill: parent
                            columns: 4
                            columnSpacing: 24
                            rowSpacing: 6

                            Label { text: "Printers"; font.bold: true; color: UM.Theme.getColor("text") }
                            Label { text: eventideBridge.printerCount.toString(); color: UM.Theme.getColor("text") }

                            Label { text: "Filaments"; font.bold: true; color: UM.Theme.getColor("text") }
                            Label { text: eventideBridge.filamentCount.toString(); color: UM.Theme.getColor("text") }

                            Label { text: "Capabilities"; font.bold: true; color: UM.Theme.getColor("text") }
                            Label { text: eventideBridge.capabilityCount.toString(); color: UM.Theme.getColor("text") }

                            Label { text: "Quality Profiles"; font.bold: true; color: UM.Theme.getColor("text") }
                            Label { text: eventideBridge.qualityCount.toString(); color: UM.Theme.getColor("text") }
                        }
                    }

                    Item { Layout.fillHeight: true }
                }
            }

            // ----------------------------------------------------------
            // Current Selection tab
            // ----------------------------------------------------------
            Item {
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 12

                    GroupBox {
                        title: "Current Cura Selection"
                        Layout.fillWidth: true

                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 10

                            GridLayout {
                                Layout.fillWidth: true
                                columns: 2
                                columnSpacing: 18
                                rowSpacing: 8

                                Label { text: "Printer"; font.bold: true; color: UM.Theme.getColor("text") }
                                Label {
                                    text: eventideBridge.activePrinterName
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                }

                                Label { text: "Printer ID"; font.bold: true; color: UM.Theme.getColor("text") }
                                Label {
                                    text: eventideBridge.activePrinterId.length > 0 ? eventideBridge.activePrinterId : "—"
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                    elide: Text.ElideMiddle
                                }

                                Label { text: "Material"; font.bold: true; color: UM.Theme.getColor("text") }
                                Label {
                                    text: eventideBridge.activeMaterialName
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                }

                                Label { text: "Material GUID / ID"; font.bold: true; color: UM.Theme.getColor("text") }
                                Label {
                                    text: eventideBridge.activeMaterialId.length > 0 ? eventideBridge.activeMaterialId : "—"
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                    elide: Text.ElideMiddle
                                }

                                Label { text: "Extruder"; font.bold: true; color: UM.Theme.getColor("text") }
                                Label {
                                    text: eventideBridge.activeExtruderPosition.toString()
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                }

                                Label { text: "Cura Nozzle"; font.bold: true; color: UM.Theme.getColor("text") }
                                Label {
                                    text: eventideBridge.activeNozzleDiameter.length > 0
                                          ? eventideBridge.activeNozzleDiameter + " mm"
                                          : "—"
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                }

                                Label { text: "Slice Hook"; font.bold: true; color: UM.Theme.getColor("text") }
                                Label {
                                    text: eventideBridge.sliceHookActive ? "ACTIVE" : "NOT ACTIVE"
                                    color: UM.Theme.getColor("text")
                                    font.bold: true
                                    Layout.fillWidth: true
                                }

                                Label { text: "Shared Status"; font.bold: true; color: UM.Theme.getColor("text") }
                                Label {
                                    text: eventideBridge.currentRegistration
                                    color: UM.Theme.getColor("text")
                                    Layout.fillWidth: true
                                }
                            }

                            RowLayout {
                                spacing: 8

                                Button {
                                    text: "Register Current Selection"
                                    onClicked: {
                                        uiStatus = eventideBridge.registerCurrentSelection(libraryPath.text)
                                        if (uiStatus.indexOf("REGISTERED:") === 0) {
                                            loadCapability()
                                        }
                                    }
                                }

                                Button {
                                    text: "Reload Current Selection"
                                    onClicked: {
                                        eventideBridge.refreshSelection()
                                        loadCapability()
                                    }
                                }

                                Button {
                                    text: "Inspect Material"
                                    onClicked: uiStatus = eventideBridge.inspectActiveMaterial()
                                }

                                Item { Layout.fillWidth: true }
                            }
                        }
                    }

                    Item { Layout.fillHeight: true }
                }
            }

            // ----------------------------------------------------------
            // Capability tab
            // ----------------------------------------------------------
            Item {
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 8

                    RowLayout {
                        Layout.fillWidth: true

                        Label {
                            text: eventideBridge.capabilityLoaded
                                  ? "Loaded revision " + eventideBridge.capabilityRevision
                                  : "No capability loaded"
                            color: UM.Theme.getColor("text")
                            font.bold: true
                        }

                        Item { Layout.fillWidth: true }

                        Label {
                            text: eventideBridge.capabilityLastCalibrated.length > 0
                                  ? "Last calibrated: " + eventideBridge.capabilityLastCalibrated
                                  : "Last calibrated: —"
                            color: UM.Theme.getColor("text")
                            opacity: 0.75
                        }
                    }

                    ScrollView {
                        id: capabilityScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true

                        ColumnLayout {
                            width: capabilityScroll.availableWidth
                            spacing: 12

                            GroupBox {
                                title: "Flow & Motion"
                                Layout.fillWidth: true

                                GridLayout {
                                    anchors.fill: parent
                                    columns: 2
                                    columnSpacing: 14
                                    rowSpacing: 8

                                    Label { text: "Max volumetric flow (mm³/s)"; color: UM.Theme.getColor("text") }
                                    TextField {
                                        id: maxFlow
                                        Layout.fillWidth: true
                                        placeholderText: "unset"
                                        selectByMouse: true
                                    }

                                    Label { text: "Max linear speed (mm/s)"; color: UM.Theme.getColor("text") }
                                    TextField {
                                        id: maxSpeed
                                        Layout.fillWidth: true
                                        placeholderText: "unset"
                                        selectByMouse: true
                                    }

                                    Label { text: "Pressure advance"; color: UM.Theme.getColor("text") }
                                    TextField {
                                        id: pressureAdvance
                                        Layout.fillWidth: true
                                        placeholderText: "unset"
                                        selectByMouse: true
                                    }


                                    Label { text: "Pressure advance G-code"; color: UM.Theme.getColor("text") }
                                    CheckBox {
                                        id: klipperPa
                                        text: "Emit Klipper SET_PRESSURE_ADVANCE"
                                    }
                                }
                            }

                            GroupBox {
                                title: "Temperature & Retraction"
                                Layout.fillWidth: true

                                GridLayout {
                                    anchors.fill: parent
                                    columns: 2
                                    columnSpacing: 14
                                    rowSpacing: 8

                                    Label { text: "Temperature offset (°C)"; color: UM.Theme.getColor("text") }
                                    TextField {
                                        id: tempOffset
                                        Layout.fillWidth: true
                                        placeholderText: "unset"
                                        selectByMouse: true
                                    }

                                    Label { text: "Retraction distance (mm)"; color: UM.Theme.getColor("text") }
                                    TextField {
                                        id: retractDistance
                                        Layout.fillWidth: true
                                        placeholderText: "unset"
                                        selectByMouse: true
                                    }

                                    Label { text: "Retraction speed (mm/s)"; color: UM.Theme.getColor("text") }
                                    TextField {
                                        id: retractSpeed
                                        Layout.fillWidth: true
                                        placeholderText: "unset"
                                        selectByMouse: true
                                    }
                                }
                            }

                            GroupBox {
                                title: "Hotend / Nozzle"
                                Layout.fillWidth: true

                                GridLayout {
                                    anchors.fill: parent
                                    columns: 2
                                    columnSpacing: 14
                                    rowSpacing: 8

                                    Label { text: "Nozzle diameter (mm)"; color: UM.Theme.getColor("text") }
                                    TextField {
                                        id: nozzleDiameter
                                        Layout.fillWidth: true
                                        readOnly: true
                                        placeholderText: eventideBridge.activeNozzleDiameter.length > 0
                                                         ? eventideBridge.activeNozzleDiameter
                                                         : "not detected"
                                        selectByMouse: true
                                    }

                                    Label { text: "Nozzle material"; color: UM.Theme.getColor("text") }
                                    TextField {
                                        id: nozzleMaterial
                                        Layout.fillWidth: true
                                        placeholderText: "e.g. Brass"
                                        selectByMouse: true
                                    }
                                }
                            }

                            GroupBox {
                                title: "CuraEngine / G-code Integration"
                                Layout.fillWidth: true

                                ColumnLayout {
                                    anchors.fill: parent
                                    spacing: 8

                                    Label {
                                        text: "Eventide now resolves the capability at slice time and changes only "
                                              + "the copied settings sent to CuraEngine. Cura user settings are not "
                                              + "modified. A final G-code guardrail enforces hard linear/flow ceilings."
                                        color: UM.Theme.getColor("text")
                                        wrapMode: Text.WordWrap
                                        Layout.fillWidth: true
                                    }

                                    RowLayout {
                                        spacing: 8

                                        Button {
                                            text: "Check Slice Resolver"
                                            onClicked: {
                                                uiStatus = eventideBridge.checkSliceResolver(libraryPath.text)
                                            }
                                        }

                                        Label {
                                            text: eventideBridge.sliceHookInstalled
                                                  ? "Slice-time hook: ACTIVE"
                                                  : "Slice-time hook: NOT READY"
                                            color: UM.Theme.getColor("text")
                                            font.bold: true
                                        }

                                        Item { Layout.fillWidth: true }
                                    }

                                    Label {
                                        text: eventideBridge.lastSliceResolution
                                        color: UM.Theme.getColor("text")
                                        opacity: 0.75
                                        Layout.fillWidth: true
                                        wrapMode: Text.WordWrap
                                    }

                                    Label {
                                        text: eventideBridge.lastGcodeGuardrailSummary
                                        color: UM.Theme.getColor("text")
                                        opacity: 0.75
                                        Layout.fillWidth: true
                                        wrapMode: Text.WordWrap
                                    }
                                }
                            }

                            GroupBox {
                                title: "Calibration Notes"
                                Layout.fillWidth: true

                                TextArea {
                                    id: capabilityNotes
                                    anchors.fill: parent
                                    implicitHeight: 100
                                    placeholderText: "Optional calibration notes"
                                    selectByMouse: true
                                    wrapMode: TextEdit.Wrap
                                }
                            }

                            Item {
                                Layout.fillWidth: true
                                implicitHeight: 8
                            }
                        }
                    }

                    // Keep actions OUTSIDE the ScrollView so they're always visible.
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        Button {
                            text: "Load Capability"
                            onClicked: loadCapability()
                        }

                        Button {
                            text: "Save Capability"
                            enabled: eventideBridge.capabilityLoaded
                            onClicked: saveCapability()
                        }

                        Item { Layout.fillWidth: true }

                        Label {
                            text: eventideBridge.capabilityLoaded
                                  ? eventideBridge.capabilityRecordId
                                  : ""
                            color: UM.Theme.getColor("text")
                            opacity: 0.55
                            elide: Text.ElideMiddle
                            Layout.preferredWidth: 300
                            horizontalAlignment: Text.AlignRight
                        }
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true

            Button {
                text: "Test Python Hook"
                onClicked: uiStatus = eventideBridge.ping()
            }

            Item { Layout.fillWidth: true }

            Button {
                text: "Close"
                onClicked: root.hide()
            }
        }
    }
}
