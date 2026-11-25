import SwiftUI

@main
@MainActor
struct WatchRTMApp: App {
    @StateObject private var link: WatchLink
    @StateObject private var monitor: WorkoutMonitor

    init() {
        let link = WatchLink()
        _link = StateObject(wrappedValue: link)
        _monitor = StateObject(wrappedValue: WorkoutMonitor(link: link))
    }

    var body: some Scene {
        WindowGroup {
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Apple Watch RTC").font(.headline)
                    Text(link.reachable ? "iPhoneに接続中" : "iPhone接続待ち").font(.caption2)
                    if let bpm = monitor.bpm { Text("\(Int(bpm.rounded())) bpm").font(.title2.monospacedDigit()) }
                    if let yaw = monitor.yaw { Text("方位 \(Int(yaw.rounded()) % 360)°").font(.caption) }
                    Text(monitor.status).font(.caption)
                    Button(monitor.active ? "計測を停止" : "計測を開始") {
                        if monitor.active { monitor.stop() }
                        else { Task { await monitor.start() } }
                    }.disabled(monitor.busy)
                    Button("睡眠ログを送信") { Task { await monitor.exportSleep() } }
                    if !monitor.sleepStatus.isEmpty { Text(monitor.sleepStatus).font(.caption2) }
                    if let error = link.error { Text(error).font(.caption2) }
                }
            }
        }
    }
}
