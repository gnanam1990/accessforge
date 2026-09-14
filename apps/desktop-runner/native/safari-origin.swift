// Read-only supervisor probe. No Apple Events, AX actions, focus changes or permission prompts.
import AppKit
import ApplicationServices
import Security
import CryptoKit

struct Request: Decodable {
    let expectedUrl: String
    let expectedBrowserVersion: String
    let includeKeyboardFocus: Bool?
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

func elementAttribute(_ element: AXUIElement, _ name: String) -> AXUIElement? {
    guard let value = attribute(element, name), CFGetTypeID(value) == AXUIElementGetTypeID()
        else { return nil }
    return (value as! AXUIElement)
}

struct KeyboardFocus {
    let element: AXUIElement
    let role: String
    let identifier: String
}

func keyboardFocus(_ app: AXUIElement, _ window: AXUIElement) -> KeyboardFocus? {
    guard let focused = elementAttribute(app, kAXFocusedUIElementAttribute),
          let active = attribute(focused, kAXFocusedAttribute) as? Bool, active,
          let owner = elementAttribute(focused, kAXWindowAttribute), CFEqual(owner, window),
          let role = attribute(focused, kAXRoleAttribute) as? String,
          ["AXTextField", "AXTextArea", "AXButton", "AXCheckBox", "AXRadioButton",
           "AXPopUpButton", "AXComboBox", "AXLink"].contains(role),
          let identifier = attribute(focused, kAXIdentifierAttribute) as? String,
          !identifier.isEmpty, identifier.utf8.count <= 1024,
          !identifier.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) })
        else { return nil }
    // A Safari toolbar field is not a page target. Walk only the bounded ancestor chain, not a
    // document tree, and require the original window plus a web-content ancestor.
    var current = focused
    var webContent = false
    for _ in 0..<32 {
        guard let parent = elementAttribute(current, kAXParentAttribute), !CFEqual(parent, current)
            else { return nil }
        if CFEqual(parent, window) {
            return webContent ? KeyboardFocus(element: focused, role: role, identifier: identifier) : nil
        }
        if attribute(parent, kAXRoleAttribute) as? String == "AXWebArea" { webContent = true }
        current = parent
    }
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
let focus = request.includeKeyboardFocus == true ? keyboardFocus(element, window) : nil
if request.includeKeyboardFocus == true && focus == nil { refuse("KEYBOARD_FOCUS_UNAVAILABLE") }

// Sample again after AX and signature reads. Do not reuse a frontmost/window/URL observation
// when another app, tab or window won focus during this probe. No claim of an atomic OS-input lock.
guard let after = NSWorkspace.shared.frontmostApplication,
      after.processIdentifier == app.processIdentifier, after.launchDate == app.launchDate,
      after.bundleIdentifier == "com.apple.Safari", !after.isTerminated,
      let afterWindow = focusedWindow(element), CFEqual(window, afterWindow),
      let afterModal = attribute(afterWindow, kAXModalAttribute) as? Bool, !afterModal,
      let afterDocument = documentURL(afterWindow),
      afterDocument == document else { refuse("BROWSER_CHANGED_DURING_SAMPLE") }

var output: [String: Any] = ["schemaVersion": 1, "status": "KNOWN",
    "bundleId": "com.apple.Safari", "pid": Int(app.processIdentifier),
    "launchedAt": launched, "browserVersion": version, "url": document]
if let focus {
    guard let repeated = keyboardFocus(element, afterWindow), CFEqual(focus.element, repeated.element),
          focus.role == repeated.role, focus.identifier == repeated.identifier,
          let finalApp = NSWorkspace.shared.frontmostApplication,
          finalApp.processIdentifier == app.processIdentifier, finalApp.launchDate == app.launchDate,
          !finalApp.isTerminated, finalApp.bundleIdentifier == "com.apple.Safari",
          let finalWindow = focusedWindow(element), CFEqual(finalWindow, window),
          let finalModal = attribute(finalWindow, kAXModalAttribute) as? Bool, !finalModal,
          documentURL(finalWindow) == document else { refuse("KEYBOARD_FOCUS_CHANGED") }
    let bytes = Data(("accessforge.keyboard-focus-identifier.v1\0" + focus.identifier).utf8)
    let fingerprint = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    output["keyboardFocus"] = ["measurementKind": "AX_KEYBOARD_FOCUS",
        "role": focus.role, "identifierDigest": fingerprint]
}
guard let encoded = try? JSONSerialization.data(withJSONObject: output, options: [.sortedKeys]),
      let text = String(data: encoded, encoding: .utf8) else { refuse("OUTPUT_UNAVAILABLE") }
print(text)
