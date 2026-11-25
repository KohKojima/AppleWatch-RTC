import Combine
import Foundation
import UIKit
import WatchConnectivity

@MainActor
final class PhoneRelay: NSObject, ObservableObject, WCSessionDelegate {
    static let shared = PhoneRelay()
    @Published private(set) var watchStatus = "Apple Watchを確認中"
    @Published private(set) var hostStatus = "転送停止中"
    @Published private(set) var durableCount = 0
    @Published private(set) var lastSent: Date?
    @Published private(set) var heart: Double?
    @Published private(set) var heading: Double?
    @Published private(set) var enabled = false

    private var durable: [SensorSample] = []
    private var motion: [SensorSample] = []
    private var endpoint: URL?
    private var bearer = ""
    private var socket: URLSessionWebSocketTask?
    private var pump: Task<Void, Never>?
    private var generation = UUID()
    private var backgroundTask = UIBackgroundTaskIdentifier.invalid

    var savedEndpoint: String { UserDefaults.standard.string(forKey: "watchrtm.endpoint") ?? "" }
    var savedToken: String { RelayStorage.token() }

    private override init() {
        super.init()
        do {
            durable = try RelayStorage.load()
            durableCount = durable.count
        } catch {
            hostStatus = "未転送データの読み込みに失敗しました"
        }
        if WCSession.isSupported() {
            WCSession.default.delegate = self
            WCSession.default.activate()
        }
        if UserDefaults.standard.bool(forKey: "watchrtm.enabled") {
            start(savedEndpoint, token: savedToken)
        }
    }

    func start(_ address: String, token: String) {
        let clean = address.trimmingCharacters(in: .whitespacesAndNewlines)
        let token = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: clean), let scheme = url.scheme, ["ws", "wss"].contains(scheme),
              url.host != nil, url.path == "/watch", url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil, token.utf8.allSatisfy({ (33...126).contains($0) }) else {
            hostStatus = "ws://PCのIPアドレス:8765/watch を入力してください"
            return
        }
        do { try RelayStorage.saveToken(token) }
        catch { hostStatus = "接続トークンを保存できませんでした"; return }
        cancelPump()
        endpoint = url
        bearer = token
        enabled = true
        lastSent = nil
        UserDefaults.standard.set(clean, forKey: "watchrtm.endpoint")
        UserDefaults.standard.set(true, forKey: "watchrtm.enabled")
        hostStatus = "転送待機中"
        resume()
    }

    func stop() {
        enabled = false
        UserDefaults.standard.set(false, forKey: "watchrtm.enabled")
        cancelPump()
        motion.removeAll()
        hostStatus = "転送停止中"
    }

    func resume() {
        guard enabled, pump == nil, let endpoint else { return }
        if UIApplication.shared.applicationState == .background {
            backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "WatchRTM transfer") { [weak self] in
                Task { @MainActor in self?.cancelPump() }
            }
            guard backgroundTask != .invalid else { return }
        }
        let current = generation
        pump = Task { [weak self] in
            guard let self else { return }
            await self.run(endpoint, generation: current)
        }
    }

    private func persist(_ queue: [SensorSample]) throws {
        try RelayStorage.save(queue)
        durable = queue
        durableCount = queue.count
    }

    private func receive(_ payload: Data) -> Bool {
        guard let samples = try? JSONDecoder().decode([SensorSample].self, from: payload) else { return false }
        var known = Set(durable.map(\.id))
        let newDurable = samples.filter { $0.kind != .motion && known.insert($0.id).inserted }
        do {
            if !newDurable.isEmpty { try persist(durable + newDurable) }
        } catch {
            hostStatus = "未転送データを保存できませんでした"
            return false
        }
        if let latest = samples.last(where: { $0.kind == .heart }) { heart = latest.bpm }
        if let latest = samples.last(where: { $0.kind == .motion }) { heading = latest.yaw }
        if enabled {
            motion = Array((motion + samples.filter { $0.kind == .motion }).suffix(120))
            resume()
        }
        return true
    }

    private func webSocket(_ endpoint: URL) -> URLSessionWebSocketTask {
        if let socket { return socket }
        var request = URLRequest(url: endpoint, timeoutInterval: 5)
        if !bearer.isEmpty { request.setValue("Bearer \(bearer)", forHTTPHeaderField: "Authorization") }
        let task = URLSession.shared.webSocketTask(with: request)
        task.maximumMessageSize = 4096
        task.resume()
        socket = task
        return task
    }

    private func run(_ endpoint: URL, generation current: UUID) async {
        defer {
            if generation == current {
                pump = nil
                endBackgroundTask()
            }
        }
        var retrySeconds = 1
        while !Task.isCancelled && generation == current {
            let now = Date().timeIntervalSince1970
            motion.removeAll { now - $0.t > 1 }
            let stream = Array(motion.prefix(48))
            let batch = SensorBatch(samples: stream + Array(durable.prefix(96 - stream.count)))
            if batch.samples.isEmpty && hostStatus == "PC接続済み" { return }
            let link = webSocket(endpoint)
            do {
                try await exchange(batch, through: link)
                guard !Task.isCancelled, generation == current else { return }
                if batch.samples.isEmpty {
                    hostStatus = "PC接続済み"
                    retrySeconds = 1
                    continue
                }
                let sent = Set(batch.samples.map(\.id))
                let remaining = durable.filter { !sent.contains($0.id) }
                if remaining.count != durable.count { try persist(remaining) }
                motion.removeAll { sent.contains($0.id) }
                lastSent = Date()
                hostStatus = "PC接続済み"
                retrySeconds = 1
            } catch {
                guard !Task.isCancelled, generation == current else { return }
                link.cancel(with: .goingAway, reason: nil)
                socket = nil
                hostStatus = "再接続待ち · \(error.localizedDescription)"
                try? await Task.sleep(for: .seconds(retrySeconds))
                retrySeconds = min(8, retrySeconds * 2)
            }
        }
    }

    private func exchange(_ batch: SensorBatch, through socket: URLSessionWebSocketTask) async throws {
        let timeout = Task {
            do {
                try await Task.sleep(for: .seconds(5))
                socket.cancel(with: .goingAway, reason: nil)
            } catch {}
        }
        defer { timeout.cancel() }
        if batch.samples.isEmpty {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                socket.sendPing { error in
                    if let error { continuation.resume(throwing: error) }
                    else { continuation.resume() }
                }
            }
            return
        }
        let payload = try JSONEncoder().encode(batch)
        try await socket.send(.string(String(decoding: payload, as: UTF8.self)))
        guard case .string(let text) = try await socket.receive(),
              let reply = try? JSONDecoder().decode(HostReply.self, from: Data(text.utf8)),
              reply.v == 1, reply.ack == batch.id else { throw RelayError.invalidReply }
    }

    private func cancelPump() {
        generation = UUID()
        pump?.cancel()
        pump = nil
        socket?.cancel(with: .goingAway, reason: nil)
        socket = nil
        endBackgroundTask()
    }

    private func endBackgroundTask() {
        if backgroundTask != .invalid {
            UIApplication.shared.endBackgroundTask(backgroundTask)
            backgroundTask = .invalid
        }
    }

    private enum RelayError: LocalizedError {
        case invalidReply
        var errorDescription: String? { "PCがデータを受理しませんでした。PCとApple Watchの時刻を確認してください。" }
    }

    nonisolated func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        let status = error?.localizedDescription ?? (state == .activated && session.isPaired ? "Apple Watchとペアリング済み" : "Apple Watch接続待ち")
        Task { @MainActor in self.watchStatus = status }
    }

    nonisolated func sessionDidBecomeInactive(_ session: WCSession) {
        Task { @MainActor in self.watchStatus = "Apple Watchを切り替え中" }
    }

    nonisolated func sessionDidDeactivate(_ session: WCSession) { session.activate() }

    nonisolated func session(_ session: WCSession, didReceiveMessageData messageData: Data,
                             replyHandler: @escaping (Data) -> Void) {
        Task { @MainActor in replyHandler(Data([self.receive(messageData) ? 1 : 0])) }
    }

    nonisolated func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any]) {
        guard let payload = userInfo["samples"] as? Data else { return }
        Task { @MainActor in _ = self.receive(payload) }
    }
}
