import Combine
import Foundation
import WatchConnectivity

@MainActor
final class WatchLink: NSObject, ObservableObject, WCSessionDelegate {
    @Published private(set) var reachable = false
    @Published private(set) var error: String?
    private var busy = false
    private var pending: [SensorSample] = []

    override init() {
        super.init()
        if WCSession.isSupported() {
            WCSession.default.delegate = self
            WCSession.default.activate()
        }
    }

    func live(_ samples: [SensorSample]) {
        pending.append(contentsOf: samples)
        let now = Date().timeIntervalSince1970
        pending.removeAll { $0.kind == .motion && now - $0.t > 0.7 }
        sendPending()
    }

    private func sendPending() {
        guard !busy, !pending.isEmpty else { return }
        let session = WCSession.default
        guard session.activationState == .activated, session.isReachable else {
            pending.removeAll { $0.kind == .motion }
            if queue(pending) { pending.removeAll() }
            return
        }
        let samples = Array(pending.prefix(64))
        let durable = samples.filter { $0.kind == .heart }
        guard let payload = try? JSONEncoder().encode(samples) else { return }
        pending.removeFirst(samples.count)
        busy = true
        session.sendMessageData(payload, replyHandler: { [weak self] reply in
            Task { @MainActor in self?.finish(reply == Data([1]), durable: durable) }
        }, errorHandler: { [weak self] _ in
            Task { @MainActor in self?.finish(false, durable: durable) }
        })
    }

    private func finish(_ accepted: Bool, durable: [SensorSample]) {
        busy = false
        if !accepted, !queue(durable) { pending.insert(contentsOf: durable, at: 0) }
        live([])
    }

    @discardableResult
    func queue(_ samples: [SensorSample]) -> Bool {
        guard !samples.isEmpty else { return true }
        let session = WCSession.default
        guard session.activationState == .activated, session.isCompanionAppInstalled else {
            error = "iPhoneの連携アプリを開いてください"
            return false
        }
        for offset in stride(from: 0, to: samples.count, by: 32) {
            let chunk = Array(samples[offset..<min(samples.count, offset + 32)])
            guard let data = try? JSONEncoder().encode(chunk) else { return false }
            session.transferUserInfo(["samples": data])
        }
        error = nil
        return true
    }

    nonisolated func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        let reachable = state == .activated && session.isReachable
        let message = error?.localizedDescription
        Task { @MainActor in
            self.reachable = reachable
            self.error = message
            self.live([])
        }
    }

    nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
        let reachable = session.isReachable
        Task { @MainActor in
            self.reachable = reachable
            self.live([])
        }
    }

    nonisolated func session(_ session: WCSession, didFinish userInfoTransfer: WCSessionUserInfoTransfer, error: Error?) {
        if let message = error?.localizedDescription {
            Task { @MainActor in self.error = message }
        }
    }
}
