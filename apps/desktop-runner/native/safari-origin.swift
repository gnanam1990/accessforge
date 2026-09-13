// Read-only supervisor probe. No Apple Events, AX actions, focus changes or permission prompts.
import AppKit
import ApplicationServices
import Security

struct Request: Decodable {
    let expectedUrl: String
    let expectedBrowserVersion: String
}

func refuse(_ reason: String) -> Never {
    // Only static codes from this file, never a URL, title, permission diagnostic or page content.
    print("{\"schemaVersion\":1,\"status\":\"UNKNOWN\",\"reason\":\"\(reason)\"}")
    exit(78)
}

func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else {
        return nil
    }
    return value
}

func focusedWindow(_ element: AXUIElement) -> AXUIElement? {
    guard let value = attribute(element, kAXFocusedWindowAttribute),
          CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
    return (value as! AXUIElement)
}

func documentURL(_ window: AXUIElement) -> String? {
    let value = attribute(window, kAXDocumentAttribute)
    if let text = value as? String { return text }
    if let url = value as? URL { return url.absoluteString }
    return nil
}

func isAppleSafari(_ pid: pid_t) -> Bool {
    var code: SecCode?
    var requirement: SecRequirement?
    guard SecCodeCopyGuestWithAttributes(nil, [kSecGuestAttributePid: pid] as CFDictionary,
                                       [], &code) == errSecSuccess,
          let code,
          SecRequirementCreateWithString("anchor apple and identifier \"com.apple.Safari\"" as CFString,
                                         [], &requirement) == errSecSuccess,
          let requirement else { return false }
    return SecCodeCheckValidity(code, [], requirement) == errSecSuccess
}

guard let input = try? FileHandle.standardInput.read(upToCount: 8193), input.count <= 8192,
      let request = try? JSONDecoder().decode(Request.self, from: input) else { refuse("INPUT_UNAVAILABLE") }
guard let app = NSWorkspace.shared.frontmostApplication,
      app.bundleIdentifier == "com.apple.Safari", !app.isTerminated,
      let launched = app.launchDate?.timeIntervalSince1970, launched > 0 else { refuse("SAFARI_NOT_FOREGROUND") }
guard isAppleSafari(app.processIdentifier) else { refuse("SAFARI_SIGNATURE_UNAVAILABLE") }
guard AXIsProcessTrusted() else { refuse("ACCESSIBILITY_UNAVAILABLE") }
guard let bundleURL = app.bundleURL,
      let version = Bundle(url: bundleURL)?.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String,
      version == request.expectedBrowserVersion else { refuse("BROWSER_VERSION_DIFFERS") }

let element = AXUIElementCreateApplication(app.processIdentifier)
guard AXUIElementSetMessagingTimeout(element, 0.4) == .success else { refuse("AX_TIMEOUT_UNAVAILABLE") }
guard let window = focusedWindow(element) else { refuse("FOCUSED_WINDOW_UNAVAILABLE") }
guard let modal = attribute(window, kAXModalAttribute) as? Bool, !modal else { refuse("MODAL_WINDOW_OR_UNKNOWN") }
guard let document = documentURL(window) else { refuse("DOCUMENT_UNAVAILABLE") }
guard document == request.expectedUrl else { refuse("DOCUMENT_DIFFERS") }

// Sample again after AX and signature reads. Do not reuse a frontmost/window/URL observation
// when another app, tab or window won focus during this probe. No claim of an atomic OS-input lock.
guard let after = NSWorkspace.shared.frontmostApplication,
      after.processIdentifier == app.processIdentifier, after.launchDate == app.launchDate,
      after.bundleIdentifier == "com.apple.Safari", !after.isTerminated,
      let afterWindow = focusedWindow(element), CFEqual(window, afterWindow),
      let afterModal = attribute(afterWindow, kAXModalAttribute) as? Bool, !afterModal,
      let afterDocument = documentURL(afterWindow),
      afterDocument == document else { refuse("BROWSER_CHANGED_DURING_SAMPLE") }

let output: [String: Any] = ["schemaVersion": 1, "status": "KNOWN",
    "bundleId": "com.apple.Safari", "pid": Int(app.processIdentifier),
    "launchedAt": launched, "browserVersion": version, "url": document]
guard let encoded = try? JSONSerialization.data(withJSONObject: output, options: [.sortedKeys]),
      let text = String(data: encoded, encoding: .utf8) else { refuse("OUTPUT_UNAVAILABLE") }
print(text)
