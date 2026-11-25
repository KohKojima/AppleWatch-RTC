import SwiftUI

@main
@MainActor
struct PhoneRTMApp: App {
    @StateObject private var relay = PhoneRelay.shared
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            RelayView(relay: relay)
                .onChange(of: phase) { _, phase in
                    if phase == .active { relay.resume() }
                }
        }
    }
}

private struct RelayView: View {
    @ObservedObject var relay: PhoneRelay
    @State private var address = ""
    @State private var token = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("PCホスト") {
                    TextField("ws://192.168.1.10:8765/watch", text: $address)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("接続トークン", text: $token)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button(relay.enabled ? "接続設定を適用" : "転送を開始") { relay.start(address, token: token) }
                    if relay.enabled { Button("転送を停止", role: .destructive) { relay.stop() } }
                }
                Section("通信状態") {
                    Text(relay.watchStatus)
                    Text(relay.hostStatus)
                    LabeledContent("未転送の心拍・睡眠", value: "\(relay.durableCount) 件")
                    if let last = relay.lastSent { LabeledContent("最終転送", value: last.formatted(date: .omitted, time: .standard)) }
                }
                Section("Apple Watchの最新値") {
                    LabeledContent("心拍数", value: relay.heart.map { "\(Int($0.rounded())) bpm" } ?? "—")
                    LabeledContent("腕の方位", value: relay.heading.map { "\(Int($0.rounded()) % 360)°" } ?? "—")
                }
            }
            .navigationTitle("Apple Watch RTC")
            .onAppear {
                address = relay.savedEndpoint
                token = relay.savedToken
            }
        }
    }
}
