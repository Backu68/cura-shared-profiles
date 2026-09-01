import QtQuick 2.7
import QtQuick.Controls 2.1
import QtQuick.Layouts 1.3
import QtQuick.Window 2.2

import UM 1.3 as UM

Window {
    id: conflictWindow
    width: 640
    height: 430
    minimumWidth: 560
    minimumHeight: 390
    visible: false
    modality: Qt.ApplicationModal
    flags: Qt.Dialog
    color: UM.Theme.getColor("main_background")
    title: "Eventide Shared Profiles - Profile Conflict"

    property string resolutionStatus: ""

    function refreshCopyName() {
        copyName.text = eventideBridge.qualityConflictSuggestedCopyName
    }

    function resolveConflict(strategy) {
        var result = eventideBridge.resolveQualityConflict(
                    eventideBridge.sharedLibraryPath,
                    strategy,
                    (strategy === "create_copy" || strategy === "keep_as_new") ? copyName.text : "")
        resolutionStatus = result
        if (result.indexOf("CONFLICT RESOLVED:") === 0) {
            if (eventideBridge.qualityConflictCount === 0) {
                conflictWindow.hide()
            } else {
                refreshCopyName()
            }
        }
    }

    onVisibleChanged: {
        if (visible) {
            refreshCopyName()
        }
    }

    Connections {
        target: eventideBridge
        function onStateChanged() {
            if (eventideBridge.qualityConflictCount === 0) {
                conflictWindow.hide()
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 12

        Label {
            text: eventideBridge.qualityConflictKind === "edit"
                  ? "Shared profile conflict detected"
                  : "Shared profile deletion conflict detected"
            color: UM.Theme.getColor("text")
            font.pixelSize: 20
            font.bold: true
            Layout.fillWidth: true
        }

        Label {
            text: "Profile: " + eventideBridge.qualityConflictName
            color: UM.Theme.getColor("text")
            font.bold: true
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }

        Label {
            text: eventideBridge.qualityConflictDetails
            color: UM.Theme.getColor("text")
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }

        RowLayout {
            Layout.fillWidth: true
            visible: eventideBridge.qualityConflictCount > 1
            Label {
                text: eventideBridge.qualityConflictPosition
                color: UM.Theme.getColor("text")
                opacity: 0.7
            }
            Item { Layout.fillWidth: true }
            Button {
                text: "Previous"
                onClicked: {
                    eventideBridge.previousQualityConflict()
                    refreshCopyName()
                }
            }
            Button {
                text: "Next"
                onClicked: {
                    eventideBridge.nextQualityConflict()
                    refreshCopyName()
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Button {
                visible: eventideBridge.qualityConflictKind === "edit"
                text: "Keep This PC"
                onClicked: resolveConflict("keep_local")
            }
            Button {
                visible: eventideBridge.qualityConflictKind === "edit"
                text: "Use Shared Version"
                onClicked: resolveConflict("use_shared")
            }
            Button {
                visible: eventideBridge.qualityConflictKind !== "edit"
                text: "Accept Deletion"
                onClicked: resolveConflict("accept_deletion")
            }
            Button {
                visible: eventideBridge.qualityConflictKind !== "edit"
                text: "Restore Profile"
                onClicked: resolveConflict("restore_profile")
            }
            Item { Layout.fillWidth: true }
        }

        Label {
            visible: eventideBridge.qualityConflictKind !== "local_delete_remote_edit"
            text: eventideBridge.qualityConflictKind === "deletion"
                  ? "Or preserve this PC's edit as a separate profile, then accept deletion of the original:"
                  : "Or preserve this PC's edit as a separate profile, then restore the shared original:"
            color: UM.Theme.getColor("text")
            opacity: 0.75
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }

        RowLayout {
            visible: eventideBridge.qualityConflictKind !== "local_delete_remote_edit"
            Layout.fillWidth: true
            TextField {
                id: copyName
                Layout.fillWidth: true
                selectByMouse: true
                placeholderText: "Name for preserved profile"
            }
            Button {
                text: eventideBridge.qualityConflictKind === "deletion"
                      ? "Keep as New Profile"
                      : "Create New Profile"
                onClicked: resolveConflict(eventideBridge.qualityConflictKind === "deletion"
                                           ? "keep_as_new" : "create_copy")
            }
        }

        Label {
            text: resolutionStatus
            visible: resolutionStatus.length > 0
            color: UM.Theme.getColor("text")
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }

        Item { Layout.fillHeight: true }

        Label {
            text: "Closing this dialog does not resolve the conflict. Eventide will continue protecting both versions, and the conflict remains available under Profiles."
            color: UM.Theme.getColor("text")
            opacity: 0.65
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }
    }
}
