import Combine
import CoreMotion
import Foundation
import HealthKit

@MainActor
final class WorkoutMonitor: NSObject, ObservableObject, HKWorkoutSessionDelegate, HKLiveWorkoutBuilderDelegate {
    @Published private(set) var status = "待機中"
    @Published private(set) var sleepStatus = ""
    @Published private(set) var bpm: Double?
    @Published private(set) var yaw: Double?
    @Published private(set) var active = false
    @Published private(set) var busy = false
    private let health = HKHealthStore()
    private let motion = CMMotionManager()
    private let heart = HKQuantityType.quantityType(forIdentifier: .heartRate)!
    private let sleep = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis)!
    private let link: WatchLink
    private var workout: HKWorkoutSession?
    private var builder: HKLiveWorkoutBuilder?
    private var buffer: [SensorSample] = []
    private var flushTask: Task<Void, Never>?
    private var origin: Double?
    private var lastHeartTime: Date?

    init(link: WatchLink) {
        self.link = link
        super.init()
    }

    func start() async {
        guard !active, !busy else { return }
        busy = true
        defer { busy = false }
        do {
            try await health.requestAuthorization(toShare: [HKObjectType.workoutType()], read: [heart])
            let configuration = HKWorkoutConfiguration()
            configuration.activityType = .other
            configuration.locationType = .unknown
            let session = try HKWorkoutSession(healthStore: health, configuration: configuration)
            let builder = session.associatedWorkoutBuilder()
            session.delegate = self
            builder.delegate = self
            let source = HKLiveWorkoutDataSource(healthStore: health, workoutConfiguration: configuration)
            source.enableCollection(for: heart, predicate: nil)
            builder.dataSource = source
            workout = session
            self.builder = builder
            lastHeartTime = nil
            bpm = nil
            yaw = nil
            let start = Date()
            session.startActivity(with: start)
            try await builder.beginCollection(at: start)
            guard workout === session else { return }
            active = true
            status = "計測中"
            startMotion()
            flushTask = Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(for: .milliseconds(100))
                    guard !Task.isCancelled, let self else { break }
                    self.flush()
                }
            }
        } catch {
            finish(at: Date(), status: "開始できません: \(error.localizedDescription)")
        }
    }

    func stop() { finish(at: Date(), status: "停止しました") }

    private func finish(at date: Date, status: String) {
        let session = workout
        let current = builder
        workout = nil
        builder = nil
        active = false
        flushTask?.cancel()
        flushTask = nil
        motion.stopDeviceMotionUpdates()
        flush()
        self.status = status
        if session?.state != .ended { session?.end() }
        if let current {
            current.endCollection(withEnd: date) { _, _ in current.discardWorkout() }
        }
    }

    private func flush() {
        link.live(buffer)
        buffer.removeAll(keepingCapacity: true)
    }

    private func startMotion() {
        guard motion.isDeviceMotionAvailable else {
            status = "心拍計測中 · モーション利用不可"
            return
        }
        origin = nil
        let available = CMMotionManager.availableAttitudeReferenceFrames()
        let magnetic = available.contains(.xMagneticNorthZVertical)
        let reference: CMAttitudeReferenceFrame = magnetic ? .xMagneticNorthZVertical : .xArbitraryZVertical
        motion.deviceMotionUpdateInterval = 1.0 / 50
        motion.startDeviceMotionUpdates(using: reference, to: .main) { [weak self] data, error in
            Task { @MainActor in
                guard let self, self.active else { return }
                if let data { self.appendMotion(data, frame: magnetic ? "magnetic" : "relative") }
                else if error != nil { self.status = "心拍計測中 · モーション取得エラー" }
            }
        }
    }

    private func appendMotion(_ data: CMDeviceMotion, frame: String) {
        let a = data.userAcceleration
        let g = data.gravity
        let norm = sqrt(g.x * g.x + g.y * g.y + g.z * g.z)
        guard norm > 0.1 else { return }
        let gx = g.x / norm, gy = g.y / norm, gz = g.z / norm
        let down = a.x * gx + a.y * gy + a.z * gz
        let horizontalScale = sqrt(max(0, 1 - gx * gx))
        let lateral = horizontalScale > 0.25 ? (a.x - down * gx) / horizontalScale : 0
        let degrees = data.attitude.yaw * 180 / .pi
        let heading = (degrees + 360).truncatingRemainder(dividingBy: 360)
        let anchor = origin ?? (Date().timeIntervalSince1970 - data.timestamp)
        origin = anchor
        yaw = heading
        if buffer.count < 64 {
            buffer.append(SensorSample(id: UUID(), kind: .motion, t: anchor + data.timestamp,
                                       ax: a.x, ay: a.y, az: a.z, lateral: lateral,
                                       vertical: -down, yaw: heading, frame: frame))
        }
    }

    func exportSleep() async {
        sleepStatus = "睡眠ログを読み込み中"
        do {
            try await health.requestAuthorization(toShare: [], read: [sleep])
            let start = Date().addingTimeInterval(-48 * 3600)
            let predicate = HKQuery.predicateForSamples(withStart: start, end: Date())
            let descriptor = HKSampleQueryDescriptor(predicates: [.categorySample(type: sleep, predicate: predicate)],
                                                     sortDescriptors: [SortDescriptor(\HKCategorySample.endDate)])
            let rows = try await descriptor.result(for: health)
            let watchRows = rows.filter { row in
                row.sourceRevision.productType?.hasPrefix("Watch") == true ||
                    row.device?.model?.localizedCaseInsensitiveContains("Watch") == true
            }
            let samples = watchRows.compactMap { row -> SensorSample? in
                guard (0...5).contains(row.value), row.endDate > row.startDate,
                      row.endDate.timeIntervalSince(row.startDate) <= 86_400 else { return nil }
                return SensorSample(id: row.uuid, kind: .sleep, t: row.endDate.timeIntervalSince1970,
                                    start: row.startDate.timeIntervalSince1970, stage: row.value)
            }
            if samples.isEmpty { sleepStatus = "Apple Watchの睡眠記録なし。ヘルスケアの設定を確認してください。" }
            else if link.queue(samples) { sleepStatus = "睡眠ログ \(samples.count) 件を転送キューへ追加" }
            else { sleepStatus = "iPhoneと接続後にもう一度送信してください" }
        } catch {
            sleepStatus = error.localizedDescription
        }
    }

    nonisolated func workoutSession(_ workoutSession: HKWorkoutSession, didChangeTo toState: HKWorkoutSessionState,
                                    from fromState: HKWorkoutSessionState, date: Date) {
        guard toState == .ended else { return }
        Task { @MainActor in
            if self.workout === workoutSession { self.finish(at: date, status: "セッションが終了しました") }
        }
    }

    nonisolated func workoutSession(_ workoutSession: HKWorkoutSession, didFailWithError error: Error) {
        let message = error.localizedDescription
        Task { @MainActor in
            if self.workout === workoutSession { self.finish(at: Date(), status: message) }
        }
    }

    nonisolated func workoutBuilderDidCollectEvent(_ workoutBuilder: HKLiveWorkoutBuilder) {}

    nonisolated func workoutBuilder(_ workoutBuilder: HKLiveWorkoutBuilder, didCollectCollectedDataOf types: Set<HKSampleType>) {
        guard let heart = HKQuantityType.quantityType(forIdentifier: .heartRate), types.contains(heart),
              let statistics = workoutBuilder.statistics(for: heart),
              let quantity = statistics.mostRecentQuantity(),
              let time = statistics.mostRecentQuantityDateInterval()?.end else { return }
        let value = quantity.doubleValue(for: HKUnit.count().unitDivided(by: .minute()))
        Task { @MainActor in
            guard self.builder === workoutBuilder, (20...250).contains(value),
                  self.lastHeartTime.map({ $0 < time }) ?? true else { return }
            self.lastHeartTime = time
            self.bpm = value
            self.buffer.append(SensorSample(id: UUID(), kind: .heart, t: time.timeIntervalSince1970, bpm: value))
        }
    }
}
