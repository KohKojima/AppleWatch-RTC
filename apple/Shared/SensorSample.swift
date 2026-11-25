import Foundation

struct SensorSample: Codable, Identifiable {
    enum Kind: String, Codable { case motion, heart, sleep }

    let id: UUID
    let kind: Kind
    let t: Double
    var ax: Double? = nil
    var ay: Double? = nil
    var az: Double? = nil
    var lateral: Double? = nil
    var vertical: Double? = nil
    var yaw: Double? = nil
    var frame: String? = nil
    var bpm: Double? = nil
    var start: Double? = nil
    var stage: Int? = nil
}

struct SensorBatch: Encodable {
    let v = 1
    let id = UUID()
    let samples: [SensorSample]
}

struct HostReply: Decodable {
    let v: Int
    let ack: UUID?
    let stored: Int?
    let error: String?
}
