import QtQuick 2.7
import QtQuick.Controls 2.1
import QtQuick.Layouts 1.3
import QtQuick.Window 2.2

import UM 1.3 as UM

Window {
    id: root

    property string uiStatus: "Ready."

    width: 860
    height: 660
    minimumWidth: 720
    minimumHeight: 540
    modality: Qt.NonModal
    color: UM.Theme.getColor("main_background")
    title: "Eventide Shared Profiles"

    function populateCapabilityFields() {
        maxFlow.text = eventideBridge.capabilityMaxVolumetricFlow
        maxSpeed.text = eventideBridge.capabilityMaxLinearSpeed
        materialFlow.text = eventideBridge.capabilityFlowPercent
        tempOffset.text = eventideBridge.capabilityTemperatureOffset
        retractDistance.text = eventideBridge.capabilityRetractionDistance
        retractSpeed.text = eventideBridge.capabilityRetractionSpeed
        nozzleDiameter.text = eventideBridge.capabilityNozzleDiameter
        nozzleMaterial.text = eventideBridge.capabilityNozzleMaterial
        capabilityNotes.text = eventideBridge.capabilityNotes
        markCalibrated.checked = false
    }

    function loadCapability() {
        if (libraryPath.text.length === 0) {
            return
        }
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
            "flow_percent": materialFlow.text,
            "temperature_offset_c": tempOffset.text,
            "retraction_distance_mm": retractDistance.text,
            "retraction_speed_mm_s": retractSpeed.text,
            "nozzle_diameter_mm": nozzleDiameter.text,
            "nozzle_material": nozzleMaterial.text,
            "notes": capabilityNotes.text,
            "mark_calibrated": markCalibrated.checked
        }
        uiStatus = eventideBridge.saveCurrentCapability(libraryPath.text, JSON.stringify(payload))
        if (uiStatus.indexOf("CAPABILITY SAVED:") === 0) {
            populateCapabilityFields()
        }
    }

    Component.onCompleted: {
        libraryPath.text = eventideBridge.sharedLibraryPath
        toolheadMaterial.text = eventideBridge.activeNozzleMaterial
        uiStatus = eventideBridge.ping()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 10

        RowLayout {
            Layout.fillWidth: true

            ColumnLayout {
                spacing: 1
                Label {
                    text: "Eventide Shared Profiles"
                    color: UM.Theme.getColor("text")
                    font.pixelSize: 20
                    font.bold: true
                }
                Label {
                    text: "Shared Cura profiles and printer/material capabilities"
                    color: UM.Theme.getColor("text")
                    opacity: 0.65
                }
            }
            Item { Layout.fillWidth: true }
            Label {
                text: "v" + eventideBridge.pluginVersion + " beta"
                color: UM.Theme.getColor("text")
                opacity: 0.6
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: statusText.implicitHeight + 14
            radius: 4
            color: UM.Theme.getColor("lining")
            Label {
                id: statusText
                anchors.fill: parent
                anchors.margins: 7
                text: root.uiStatus
                color: UM.Theme.getColor("text")
                wrapMode: Text.WordWrap
            }
        }

        TabBar {
            id: tabs
            Layout.fillWidth: true
            onCurrentIndexChanged: {
                if (currentIndex === 1) {
                    eventideBridge.refreshSelection()
                    toolheadMaterial.text = eventideBridge.activeNozzleMaterial
                } else if (currentIndex === 2) {
                    eventideBridge.refreshSelection()
                    loadCapability()
                }
            }

            TabButton { text: "Library" }
            TabButton { text: "Profiles" }
            TabButton { text: "Capability" }
        }

        StackLayout {
            currentIndex: tabs.currentIndex
            Layout.fillWidth: true
            Layout.fillHeight: true

            // Library -------------------------------------------------
            Item {
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 12

                    GroupBox {
                        title: "Shared library"
                        Layout.fillWidth: true

                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 8

                            RowLayout {
                                Layout.fillWidth: true
                                TextField {
                                    id: libraryPath
                                    Layout.fillWidth: true
                                    selectByMouse: true
                                    placeholderText: "\\\\path\\to\\share"
                                }
                                Button {
                                    text: "Browse…"
                                    onClicked: {
                                        var selected = eventideBridge.browseForLibraryPath()
                                        if (selected.length > 0) {
                                            libraryPath.text = selected
                                        }
                                    }
                                }
                                Button {
                                    text: "Connect"
                                    highlighted: true
                                    onClicked: uiStatus = eventideBridge.connectLibrary(libraryPath.text)
                                }
                            }

                            Label {
                                text: eventideBridge.lastLibraryEvent
                                color: UM.Theme.getColor("text")
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                        }
                    }

                    GroupBox {
                        title: "Library contents"
                        Layout.fillWidth: true

                        RowLayout {
                            anchors.fill: parent
                            spacing: 18
                            Label { text: eventideBridge.printerCount + " printers"; color: UM.Theme.getColor("text") }
                            Label { text: eventideBridge.filamentCount + " materials"; color: UM.Theme.getColor("text") }
                            Label { text: eventideBridge.qualityCount + " quality profiles"; color: UM.Theme.getColor("text") }
                            Label { text: eventideBridge.capabilityCount + " capabilities"; color: UM.Theme.getColor("text") }
                            Item { Layout.fillWidth: true }
                        }
                    }

                    GroupBox {
                        title: "Synchronization"
                        Layout.fillWidth: true

                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 8
                            Label {
                                text: eventideBridge.lastSyncSummary
                                color: UM.Theme.getColor("text")
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                            Label {
                                text: eventideBridge.lastQualitySyncSummary
                                color: UM.Theme.getColor("text")
                                opacity: 0.8
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                            RowLayout {
                                Button {
                                    text: "Sync now"
                                    onClicked: {
                                        uiStatus = eventideBridge.syncLibraryToCura(libraryPath.text)
                                        eventideBridge.refreshSelection()
                                    }
                                }
                                Item { Layout.fillWidth: true }
                                CheckBox {
                                    id: showAdvanced
                                    text: "Advanced"
                                }
                            }
                        }
                    }

                    GroupBox {
                        title: "Advanced"
                        Layout.fillWidth: true
                        visible: showAdvanced.checked

                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 8
                            RowLayout {
                                Button {
                                    text: "Create library"
                                    onClicked: uiStatus = eventideBridge.initializeLibrary(libraryPath.text)
                                }
                                Button {
                                    text: "Validate"
                                    onClicked: uiStatus = eventideBridge.validateLibrary(libraryPath.text)
                                }
                                Button {
                                    text: "Export diagnostics"
                                    onClicked: uiStatus = eventideBridge.exportDiagnostics()
                                }
                                Item { Layout.fillWidth: true }
                                Label {
                                    text: eventideBridge.sliceHookActive ? "Slice engine ready" : "Slice engine not ready"
                                    color: UM.Theme.getColor("text")
                                    opacity: 0.65
                                }
                            }
                            Label {
                                text: eventideBridge.libraryValidationSummary
                                color: UM.Theme.getColor("text")
                                opacity: 0.7
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                        }
                    }

                    Item { Layout.fillHeight: true }
                }
            }

            // Profiles ------------------------------------------------
            Item {
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 12

                    GroupBox {
                        title: "Current Cura setup"
                        Layout.fillWidth: true

                        GridLayout {
                            anchors.fill: parent
                            columns: 2
                            columnSpacing: 18
                            rowSpacing: 8

                            Label { text: "Printer"; font.bold: true; color: UM.Theme.getColor("text") }
                            Label { text: eventideBridge.activePrinterName; color: UM.Theme.getColor("text"); Layout.fillWidth: true }

                            Label { text: "Material"; font.bold: true; color: UM.Theme.getColor("text") }
                            Label { text: eventideBridge.activeMaterialName; color: UM.Theme.getColor("text"); Layout.fillWidth: true }

                            Label { text: "Nozzle"; font.bold: true; color: UM.Theme.getColor("text") }
                            Label {
                                text: eventideBridge.activeNozzleDiameter.length > 0 ? eventideBridge.activeNozzleDiameter + " mm" : "—"
                                color: UM.Theme.getColor("text")
                                Layout.fillWidth: true
                            }

                            Label { text: "Shared"; font.bold: true; color: UM.Theme.getColor("text") }
                            Label { text: eventideBridge.currentRegistration; color: UM.Theme.getColor("text"); Layout.fillWidth: true }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: "Nozzle material"; color: UM.Theme.getColor("text") }
                        TextField {
                            id: toolheadMaterial
                            Layout.fillWidth: true
                            placeholderText: "e.g. Brass"
                            selectByMouse: true
                        }
                        Button {
                            text: "Save"
                            onClicked: {
                                uiStatus = eventideBridge.setActiveNozzleMaterial(toolheadMaterial.text)
                                toolheadMaterial.text = eventideBridge.activeNozzleMaterial
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Button {
                            text: "Share current setup"
                            highlighted: true
                            onClicked: {
                                uiStatus = eventideBridge.registerCurrentSelection(libraryPath.text)
                                loadCapability()
                            }
                        }
                        Label {
                            text: "Publishes this printer, material and capability to the shared library."
                            color: UM.Theme.getColor("text")
                            opacity: 0.7
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }
                    }

                    GroupBox {
                        title: "Quality profiles"
                        Layout.fillWidth: true
                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 6
                            Label {
                                text: eventideBridge.lastQualitySyncSummary
                                color: UM.Theme.getColor("text")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                            Label {
                                text: "Saved Cura custom profiles for shared printers are synchronized automatically while Cura is running."
                                color: UM.Theme.getColor("text")
                                opacity: 0.65
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    GroupBox {
                        title: "Conflict requires attention"
                        visible: eventideBridge.qualityConflictCount > 0
                        Layout.fillWidth: true

                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 8

                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "Profile: " + eventideBridge.qualityConflictName
                                    color: UM.Theme.getColor("text")
                                    font.bold: true
                                    Layout.fillWidth: true
                                }
                                Label {
                                    text: eventideBridge.qualityConflictPosition
                                    color: UM.Theme.getColor("text")
                                    opacity: 0.7
                                    visible: eventideBridge.qualityConflictCount > 1
                                }
                                Button {
                                    text: "‹"
                                    visible: eventideBridge.qualityConflictCount > 1
                                    onClicked: {
                                        eventideBridge.previousQualityConflict()
                                        conflictCopyName.text = eventideBridge.qualityConflictSuggestedCopyName
                                    }
                                }
                                Button {
                                    text: "›"
                                    visible: eventideBridge.qualityConflictCount > 1
                                    onClicked: {
                                        eventideBridge.nextQualityConflict()
                                        conflictCopyName.text = eventideBridge.qualityConflictSuggestedCopyName
                                    }
                                }
                            }

                            Label {
                                text: eventideBridge.qualityConflictDetails
                                color: UM.Theme.getColor("text")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                Button {
                                    visible: eventideBridge.qualityConflictKind === "edit"
                                    text: "Keep This PC"
                                    onClicked: uiStatus = eventideBridge.resolveQualityConflict(libraryPath.text, "keep_local", "")
                                }
                                Button {
                                    visible: eventideBridge.qualityConflictKind === "edit"
                                    text: "Use Shared Version"
                                    onClicked: uiStatus = eventideBridge.resolveQualityConflict(libraryPath.text, "use_shared", "")
                                }
                                Button {
                                    visible: eventideBridge.qualityConflictKind !== "edit"
                                    text: "Accept Deletion"
                                    onClicked: uiStatus = eventideBridge.resolveQualityConflict(libraryPath.text, "accept_deletion", "")
                                }
                                Button {
                                    visible: eventideBridge.qualityConflictKind !== "edit"
                                    text: "Restore Profile"
                                    onClicked: uiStatus = eventideBridge.resolveQualityConflict(libraryPath.text, "restore_profile", "")
                                }
                                Item { Layout.fillWidth: true }
                            }

                            Label {
                                visible: eventideBridge.qualityConflictKind !== "local_delete_remote_edit"
                                text: eventideBridge.qualityConflictKind === "deletion"
                                      ? "Or preserve this PC's edit as a separate profile before accepting deletion:"
                                      : "Or preserve this PC's edit as a separate profile:"
                                color: UM.Theme.getColor("text")
                                opacity: 0.75
                            }
                            RowLayout {
                                visible: eventideBridge.qualityConflictKind !== "local_delete_remote_edit"
                                Layout.fillWidth: true
                                TextField {
                                    id: conflictCopyName
                                    Layout.fillWidth: true
                                    selectByMouse: true
                                    text: eventideBridge.qualityConflictSuggestedCopyName
                                    placeholderText: "Name for preserved profile"
                                }
                                Button {
                                    text: eventideBridge.qualityConflictKind === "deletion"
                                          ? "Keep as New Profile"
                                          : "Create New Profile"
                                    onClicked: uiStatus = eventideBridge.resolveQualityConflict(
                                        libraryPath.text,
                                        eventideBridge.qualityConflictKind === "deletion" ? "keep_as_new" : "create_copy",
                                        conflictCopyName.text)
                                }
                            }
                        }
                    }

                    Item { Layout.fillHeight: true }
                }
            }

            // Capability ----------------------------------------------
            Item {
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: eventideBridge.capabilityLoaded
                                  ? eventideBridge.activePrinterName + " + " + eventideBridge.activeMaterialName
                                  : "No capability for the current selection"
                            color: UM.Theme.getColor("text")
                            font.bold: true
                        }
                        Item { Layout.fillWidth: true }
                        Label {
                            text: eventideBridge.capabilityLoaded ? "Calibration: " + eventideBridge.capabilityCalibrationStatus : ""
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
                                title: "Flow & motion"
                                Layout.fillWidth: true
                                GridLayout {
                                    anchors.fill: parent
                                    columns: 2
                                    columnSpacing: 14
                                    rowSpacing: 8

                                    Label { text: "Max volumetric flow (mm³/s)"; color: UM.Theme.getColor("text") }
                                    TextField { id: maxFlow; Layout.fillWidth: true; placeholderText: "inherit Cura"; selectByMouse: true }

                                    Label { text: "Max linear speed (mm/s)"; color: UM.Theme.getColor("text") }
                                    TextField { id: maxSpeed; Layout.fillWidth: true; placeholderText: "inherit Cura"; selectByMouse: true }

                                    Label { text: "Material flow (%)"; color: UM.Theme.getColor("text") }
                                    TextField { id: materialFlow; Layout.fillWidth: true; placeholderText: "inherit Cura"; selectByMouse: true }

                                }
                            }

                            GroupBox {
                                title: "Temperature & retraction"
                                Layout.fillWidth: true
                                GridLayout {
                                    anchors.fill: parent
                                    columns: 2
                                    columnSpacing: 14
                                    rowSpacing: 8

                                    Label { text: "Temperature offset (°C)"; color: UM.Theme.getColor("text") }
                                    TextField { id: tempOffset; Layout.fillWidth: true; placeholderText: "inherit Cura"; selectByMouse: true }

                                    Label { text: "Retraction distance (mm)"; color: UM.Theme.getColor("text") }
                                    TextField { id: retractDistance; Layout.fillWidth: true; placeholderText: "inherit Cura"; selectByMouse: true }

                                    Label { text: "Retraction speed (mm/s)"; color: UM.Theme.getColor("text") }
                                    TextField { id: retractSpeed; Layout.fillWidth: true; placeholderText: "inherit Cura"; selectByMouse: true }
                                }
                            }

                            GroupBox {
                                title: "Toolhead"
                                Layout.fillWidth: true
                                GridLayout {
                                    anchors.fill: parent
                                    columns: 2
                                    columnSpacing: 14
                                    rowSpacing: 8

                                    Label { text: "Nozzle diameter (mm)"; color: UM.Theme.getColor("text") }
                                    TextField { id: nozzleDiameter; Layout.fillWidth: true; readOnly: true; placeholderText: "not detected"; selectByMouse: true }

                                    Label { text: "Nozzle material"; color: UM.Theme.getColor("text") }
                                    TextField { id: nozzleMaterial; Layout.fillWidth: true; placeholderText: "e.g. Brass"; selectByMouse: true }
                                }
                            }

                            GroupBox {
                                title: "Calibration notes"
                                Layout.fillWidth: true
                                ColumnLayout {
                                    anchors.fill: parent
                                    spacing: 6
                                    TextArea {
                                        id: capabilityNotes
                                        Layout.fillWidth: true
                                        implicitHeight: 90
                                        placeholderText: "Optional notes"
                                        selectByMouse: true
                                        wrapMode: TextEdit.Wrap
                                    }
                                    RowLayout {
                                        CheckBox { id: markCalibrated; text: "Mark this save as calibrated" }
                                        Item { Layout.fillWidth: true }
                                        Label {
                                            text: eventideBridge.capabilityLastCalibrated.length > 0 ? "Last calibrated " + eventideBridge.capabilityLastCalibrated : ""
                                            color: UM.Theme.getColor("text")
                                            opacity: 0.6
                                        }
                                    }
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Button {
                            text: "Reload"
                            onClicked: loadCapability()
                        }
                        Item { Layout.fillWidth: true }
                        Button {
                            text: "Save capability"
                            highlighted: true
                            enabled: eventideBridge.capabilityLoaded
                            onClicked: saveCapability()
                        }
                    }
                }
            }
        }
    }
}
