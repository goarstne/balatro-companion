//@ pragma AppId org.omarchy.balatro-hud
//@ pragma ShellId balatro-hud
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland

ShellRoot {
    id: root
    property var snapshot: ({})
    property string readError: ""
    property double now: Date.now() / 1000
    readonly property var stats: snapshot.stats || ({})
    readonly property var rec: snapshot.recommendation || ({})
    readonly property bool disconnected: now - (snapshot.heartbeat || 0) > 12
    readonly property bool current: snapshot.status === "ready" && !disconnected && !readError
    readonly property color green: "#77f5ad"
    readonly property color cyan: "#6cdef2"
    readonly property color muted: "#8fa8ae"
    readonly property color amber: "#f4bc71"

    function number(value) {
        return value === null || value === undefined ? "—" : Number(value).toLocaleString(Qt.locale("en_US"), "f", Number.isInteger(value) ? 0 : 1)
    }
    function value(v) { return v === null || v === undefined || v === "" ? "—" : String(v) }
    function pct(v) { return v === null || v === undefined ? "—" : Math.round(v * 100) + "%" }
    function readSnapshot() {
        try {
            var parsed = JSON.parse(source.text())
            if (parsed.schema !== 1) throw new Error("Unknown data format")
            snapshot = parsed
            readError = ""
        } catch (e) {
            readError = "Could not read the advisor data"
            console.warn("Balatro HUD:", e)
        }
    }

    FileView {
        id: source
        path: Quickshell.env("BALATRO_HUD_FILE")
        watchChanges: true
        onFileChanged: reload()
        onLoaded: root.readSnapshot()
        onLoadFailed: root.readError = "Lost connection to the advisor"
    }
    Timer { interval: 1000; running: true; repeat: true; onTriggered: root.now = Date.now() / 1000 }

    component Label: Text {
        color: "#e0eceb"
        font.family: "monospace"
        font.pixelSize: 13
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }
    component Card: Rectangle {
        default property alias contents: body.data
        color: "#101d23"
        border.color: "#294540"
        radius: 7
        implicitHeight: body.implicitHeight + 24
        ColumnLayout {
            id: body
            anchors { top: parent.top; left: parent.left; right: parent.right; margins: 12 }
            spacing: 8
        }
    }

    FloatingWindow {
        id: window
        title: "Balatro // Companion"
        implicitWidth: 720
        implicitHeight: 780
        minimumSize: Qt.size(420, 310)
        color: "#0a1117"
        property bool compact: false
        onClosed: Qt.quit()

        Rectangle {
            anchors.fill: parent
            color: "transparent"
            border.color: "#42745e"
            radius: 8
        }
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 10

            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                Item {
                    Layout.fillWidth: true
                    implicitHeight: 32
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "⠿  BALATRO // COMPANION"
                        font.bold: true
                        font.pixelSize: 15
                        color: root.green
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.SizeAllCursor
                        onPressed: window.startSystemMove()
                        Accessible.name: "Move widget"
                    }
                }
                ToolButton {
                    text: window.compact ? "+" : "−"
                    Accessible.name: "Toggle compact view"
                    ToolTip.visible: hovered
                    ToolTip.text: window.compact ? "Show details" : "Compact view"
                    onClicked: {
                        window.compact = !window.compact
                        // Wayland window sizes are controlled by the compositor after mapping.
                        Hyprland.dispatch('hl.dsp.window.resize({ window = "class:^org[.]omarchy[.]balatro-hud$", x = ' +
                                          (window.compact ? 480 : 720) + ', y = ' + (window.compact ? 385 : 780) + ', relative = false })')
                    }
                }
                ToolButton {
                    text: "_"
                    Accessible.name: "Minimize"
                    ToolTip.visible: hovered
                    ToolTip.text: "Minimize"
                    onClicked: window.minimized = true
                }
                ToolButton {
                    text: "×"
                    Accessible.name: "Close widget and advisor"
                    ToolTip.visible: hovered
                    ToolTip.text: "Quit widget and advisor"
                    onClicked: Qt.quit()
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Rectangle {
                    width: 7; height: 7; radius: 4
                    color: root.current ? root.green : root.amber
                }
                Label {
                    text: root.readError ? "DATA ERROR" : root.disconnected ? "OFFLINE" :
                          root.snapshot.status === "analyzing" ? "THINKING …" : root.current ? "LIVE" : "LAST STATE"
                    color: root.current ? root.green : root.amber
                    font.bold: true
                }
                Label { text: root.snapshot.source || "WAITING FOR GAME"; color: root.muted; font.pixelSize: 11 }
                Item { Layout.fillWidth: true }
                Label {
                    text: "Analysis " + root.number(root.snapshot.duration) + " s"
                    color: root.muted
                    font.pixelSize: 11
                }
            }
            Label {
                Layout.fillWidth: true
                text: root.readError || (root.disconnected ? "Advisor not responding – values are stale" : root.snapshot.message || "Waiting for the game window …")
                color: root.current ? root.muted : root.amber
                font.pixelSize: 11
            }
            Flickable {
                id: scroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: width
                contentHeight: body.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { }

                ColumnLayout {
                    id: body
                    width: scroll.width
                    spacing: 10

                    Card {
                        Layout.fillWidth: true
                        Item {
                            Layout.fillWidth: true
                            implicitHeight: 43
                            Row {
                                anchors.fill: parent
                                Repeater {
                                    model: [{label: "ANTE", value: root.stats.ante}, {label: "ROUND", value: root.stats.round},
                                            {label: "HANDS", value: root.stats.hands_left}, {label: "DISCARDS", value: root.stats.discards_left}]
                                    Column {
                                        required property var modelData
                                        width: parent.width / 4
                                        spacing: 3
                                        Label { text: modelData.label; color: root.muted; font.pixelSize: 10 }
                                        Label { text: root.number(modelData.value); color: root.cyan; font.pixelSize: 20; font.bold: true }
                                    }
                                }
                            }
                        }
                    }
                    Card {
                        Layout.fillWidth: true
                        border.color: root.current ? "#43865e" : "#294540"
                        Label { text: root.current ? "NEXT MOVE" : "RECOMMENDATION · LAST STATE"; color: root.green; font.pixelSize: 11 }
                        Label {
                            Layout.fillWidth: true
                            text: "> " + (root.rec.title || "Waiting for the game …")
                            font.pixelSize: 21; font.bold: true
                            color: root.current ? root.green : root.muted
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: (root.rec.cards || []).length > 0
                            text: (root.rec.cards || []).join("  ·  ")
                            color: root.cyan
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            visible: root.rec.score !== null && root.rec.score !== undefined
                            Label {
                                Layout.fillWidth: true
                                text: root.number(root.rec.chips) + " × " + root.number(root.rec.mult) + " = " + root.number(root.rec.score)
                                color: root.cyan; font.pixelSize: 23; font.bold: true
                            }
                            Label { text: root.value(root.rec.hand_type); color: root.muted; font.pixelSize: 11 }
                        }
                        Label {
                            visible: root.rec.title === "DISCARD"
                            text: "Score above: best hand before the discard"
                            color: root.amber; font.pixelSize: 11
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: !!root.stats.blind_chips
                            text: root.value(root.stats.blind) + "  ·  " + root.number(root.stats.score) + " / " + root.number(root.stats.blind_chips)
                            color: root.muted; font.pixelSize: 12
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            visible: !!root.stats.blind_chips
                            height: 6; radius: 3; color: "#263137"
                            Rectangle {
                                height: parent.height; radius: 3
                                width: parent.width * Math.max(0, Math.min(1, (root.rec.projected || root.stats.score || 0) / (root.stats.blind_chips || 1)))
                                color: "#29646b"
                            }
                            Rectangle {
                                height: parent.height; radius: 3
                                width: parent.width * Math.max(0, Math.min(1, (root.stats.score || 0) / (root.stats.blind_chips || 1)))
                                color: root.green
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: root.rec.projected !== null && root.rec.projected !== undefined
                            text: "After move: " + root.number(root.rec.projected) + "  ·  Still needed: " + root.number(root.rec.need)
                            color: root.muted; font.pixelSize: 11
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: window.compact
                        text: root.rec.reason || "Open the game; advice starts automatically."
                        color: root.muted; font.pixelSize: 12
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        visible: !window.compact
                        columns: scroll.width >= 600 ? 2 : 1
                        columnSpacing: 10; rowSpacing: 10

                        Card {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignTop
                            Label { text: "STRATEGY"; font.bold: true; color: root.green }
                            Label {
                                Layout.fillWidth: true
                                text: "NOW\n" + (root.rec.reason || "The game state is read as soon as the game is visible.")
                                font.pixelSize: 12
                            }
                            Repeater {
                                model: root.snapshot.strategy || []
                                ColumnLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: 4
                                    Label { text: modelData.title; color: root.cyan; font.pixelSize: 11; font.bold: true }
                                    Label { Layout.fillWidth: true; text: modelData.text; font.pixelSize: 12 }
                                }
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignTop
                            spacing: 10
                            Card {
                                Layout.fillWidth: true
                                Label { text: "CURRENT RUN"; font.bold: true; color: root.green }
                                Repeater {
                                    model: [
                                        ["Phase", root.value(root.snapshot.phase)],
                                        ["Money / interest", "$" + root.number(root.stats.money) + " / $" + root.number(root.stats.interest)],
                                        ["Jokers", root.number(root.stats.joker_count) + " / " + root.number(root.stats.joker_slots)],
                                        ["Consumables", root.number(root.stats.consumable_count) + " / " + root.number(root.stats.consumable_slots)],
                                        ["Deck left / total", root.number(root.stats.deck_count) + " / " + root.number(root.stats.full_deck_count)],
                                        ["Focus", root.value(root.stats.focus) + " · Lvl " + root.number(root.stats.focus_level)],
                                        ["Deck", root.value(root.stats.deck_type)]
                                    ]
                                    RowLayout {
                                        required property var modelData
                                        Layout.fillWidth: true
                                        Label { text: modelData[0]; color: root.muted; font.pixelSize: 11 }
                                        Label { Layout.fillWidth: true; horizontalAlignment: Text.AlignRight; text: modelData[1]; color: root.cyan; font.pixelSize: 12 }
                                    }
                                }
                            }
                            Card {
                                Layout.fillWidth: true
                                visible: (root.snapshot.alternatives || []).length > 0
                                Label { text: "ALTERNATIVE HANDS"; color: root.green; font.bold: true }
                                Repeater {
                                    model: root.snapshot.alternatives || []
                                    ColumnLayout {
                                        required property var modelData
                                        Layout.fillWidth: true
                                        RowLayout {
                                            Layout.fillWidth: true
                                            Label { Layout.fillWidth: true; text: modelData.hand; font.pixelSize: 12 }
                                            Label { text: root.number(modelData.score); color: root.cyan; font.bold: true }
                                        }
                                        Label { Layout.fillWidth: true; text: modelData.cards; font.pixelSize: 10; color: root.muted }
                                    }
                                }
                            }
                            Card {
                                Layout.fillWidth: true
                                visible: root.rec.samples > 0
                                Label { text: "ROUND SIMULATION"; color: root.green; font.bold: true }
                                Label {
                                    Layout.fillWidth: true
                                    text: "Play now: " + root.pct(root.rec.play_win) +
                                          (root.rec.discard_win !== null ? "\nDiscard: " + root.pct(root.rec.discard_win) : "") +
                                          "\n" + root.number(root.rec.samples) + " samples · estimated win chance"
                                    font.pixelSize: 12
                                }
                            }
                            Card {
                                Layout.fillWidth: true
                                visible: (root.snapshot.history || []).length > 1
                                Label { text: "ROUND SCORE · OBSERVED"; color: root.green; font.pixelSize: 11 }
                                Canvas {
                                    id: chart
                                    Layout.fillWidth: true
                                    implicitHeight: 55
                                    property var points: root.snapshot.history || []
                                    onPointsChanged: requestPaint()
                                    onWidthChanged: requestPaint()
                                    onPaint: {
                                        var ctx = getContext("2d")
                                        ctx.clearRect(0, 0, width, height)
                                        if (points.length < 2) return
                                        var max = Math.max(1, ...points)
                                        ctx.strokeStyle = "#6cdef2"; ctx.lineWidth = 2; ctx.beginPath()
                                        for (var i = 0; i < points.length; i++) {
                                            var x = 3 + (width - 6) * i / (points.length - 1)
                                            var y = height - 3 - points[i] / max * (height - 6)
                                            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                                        }
                                        ctx.stroke()
                                    }
                                }
                            }
                        }
                    }
                    Card {
                        Layout.fillWidth: true
                        visible: !window.compact && (root.rec.joker_order || []).length > 0
                        Label { text: "REORDER JOKERS · MANUAL"; color: root.green; font.bold: true }
                        Label { Layout.fillWidth: true; text: (root.rec.joker_order || []).join(" → "); font.pixelSize: 12 }
                        Label { text: root.number(root.rec.score) + " → " + root.number(root.rec.reordered_score) + " points"; color: root.cyan }
                    }
                    Card {
                        Layout.fillWidth: true
                        visible: !window.compact && (root.rec.card_order || []).length > 0
                        Label { text: "REORDER HAND · MANUAL"; color: root.green; font.bold: true }
                        Label { Layout.fillWidth: true; text: (root.rec.card_order || []).join(" → "); font.pixelSize: 12 }
                    }
                    Card {
                        Layout.fillWidth: true
                        visible: !window.compact && (root.snapshot.shop_actions || []).length > 0
                        Label { text: "SHOP PLAN"; color: root.green; font.bold: true }
                        Repeater {
                            model: root.snapshot.shop_actions || []
                            Label {
                                required property var modelData
                                Layout.fillWidth: true
                                text: "› " + modelData.why
                                font.pixelSize: 12
                            }
                        }
                        Label { text: "Offer values are strategy heuristics"; font.pixelSize: 10; color: root.muted }
                        Repeater {
                            model: root.snapshot.offers || []
                            Label {
                                required property var modelData
                                Layout.fillWidth: true
                                text: modelData.name + " · $" + root.number(modelData.price) + " · value " + root.number(modelData.value) + "\n" + modelData.why
                                font.pixelSize: 12
                                color: modelData.buyable ? "#e0eceb" : root.muted
                            }
                        }
                    }
                    Card {
                        Layout.fillWidth: true
                        visible: !window.compact && (root.snapshot.pack_items || []).length > 0
                        Label { text: root.snapshot.pack || "PACK"; color: root.green; font.bold: true }
                        Repeater {
                            model: root.snapshot.pack_items || []
                            Label {
                                required property var modelData
                                Layout.fillWidth: true
                                text: (modelData.chosen ? "> " : "  ") + modelData.name
                                color: modelData.chosen ? root.green : root.muted
                            }
                        }
                    }
                    Card {
                        Layout.fillWidth: true
                        visible: !window.compact && (root.snapshot.jokers || []).length > 0
                        Label { text: "JOKERS & CONSUMABLES"; color: root.green; font.bold: true }
                        Label { Layout.fillWidth: true; text: (root.snapshot.jokers || []).join("\n"); font.pixelSize: 12 }
                        Label { Layout.fillWidth: true; text: "Consumables: " + ((root.snapshot.consumables || []).join(" · ") || "—"); color: root.muted; font.pixelSize: 12 }
                    }
                    Card {
                        Layout.fillWidth: true
                        visible: !window.compact && (root.rec.log || []).length > 0
                        Label { text: "CALCULATION"; color: root.green; font.bold: true }
                        Label { Layout.fillWidth: true; text: (root.rec.log || []).join("\n"); font.pixelSize: 11; color: root.muted }
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: !window.compact
                        text: (root.snapshot.warnings || []).map(x => "△ " + x).join("\n")
                        font.pixelSize: 11; color: root.amber
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                text: "ADVICE ONLY  ·  drag the title bar  ·  Super + right mouse: resize"
                font.pixelSize: 10; color: root.muted
            }
        }
        MouseArea {
            anchors { right: parent.right; bottom: parent.bottom }
            width: 14; height: 14
            cursorShape: Qt.SizeFDiagCursor
            onPressed: window.startSystemResize(Qt.RightEdge | Qt.BottomEdge)
            Accessible.name: "Resize widget"
        }
    }
}
