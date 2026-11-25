import Foundation
import Security

enum RelayStorage {
    private static let key: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "SI2026.WatchRTM",
        kSecAttrAccount as String: "ingest-token"
    ]

    static func token() -> String {
        var query = key
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var match: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &match) == errSecSuccess,
              let bytes = match as? Data else { return "" }
        return String(decoding: bytes, as: UTF8.self)
    }

    static func saveToken(_ value: String) throws {
        let attributes: [String: Any] = [kSecValueData as String: Data(value.utf8)]
        var status = SecItemUpdate(key as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var item = key.merging(attributes) { _, new in new }
            item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            status = SecItemAdd(item as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
    }

    private static func spoolURL() throws -> URL {
        let base = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                               appropriateFor: nil, create: true)
        var folder = base.appendingPathComponent("WatchRTM", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try folder.setResourceValues(values)
        return folder.appendingPathComponent("outbox.json")
    }

    static func load() throws -> [SensorSample] {
        let file = try spoolURL()
        guard FileManager.default.fileExists(atPath: file.path) else { return [] }
        return try JSONDecoder().decode([SensorSample].self, from: Data(contentsOf: file))
    }

    static func save(_ samples: [SensorSample]) throws {
        let data = try JSONEncoder().encode(samples)
        try data.write(to: spoolURL(), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }
}
