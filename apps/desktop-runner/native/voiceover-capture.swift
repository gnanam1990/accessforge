// Read-only last-phrase channel probe. Never start an application, output speech, copy, or prompt.
import AppKit
import Carbon
import Security

func refuse() -> Never {
    print("{\"schemaVersion\":1,\"status\":\"UNKNOWN\"}")
    exit(78)
}

func appleVoiceOver(_ pid: pid_t) -> Bool {
    var code: SecCode?
    var requirement: SecRequirement?
    guard SecCodeCopyGuestWithAttributes(nil, [kSecGuestAttributePid: pid] as CFDictionary,
                                       [], &code) == errSecSuccess,
          let code,
          SecRequirementCreateWithString("anchor apple and identifier \"com.apple.VoiceOver\"" as CFString,
                                         [], &requirement) == errSecSuccess,
          let requirement else { return false }
    return SecCodeCheckValidity(code, [], requirement) == errSecSuccess
}

func property(_ code: OSType, of container: NSAppleEventDescriptor) -> NSAppleEventDescriptor {
    let record = NSAppleEventDescriptor.record()
    record.setDescriptor(NSAppleEventDescriptor(typeCode: OSType(typeProperty)), forKeyword: AEKeyword(keyAEDesiredClass))
    record.setDescriptor(NSAppleEventDescriptor(enumCode: OSType(formPropertyID)), forKeyword: AEKeyword(keyAEKeyForm))
    record.setDescriptor(NSAppleEventDescriptor(typeCode: code), forKeyword: AEKeyword(keyAEKeyData))
    record.setDescriptor(container, forKeyword: AEKeyword(keyAEContainer))
    guard let result = record.coerce(toDescriptorType: DescType(typeObjectSpecifier)) else { refuse() }
    return result
}

let apps = NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.VoiceOver")
guard apps.count == 1, let app = apps.first, !app.isTerminated,
      app.processIdentifier > 0, let launched = app.launchDate,
      appleVoiceOver(app.processIdentifier) else { refuse() }
// A process-id target cannot launch/relaunch VoiceOver if it exits between checks.
let target = NSAppleEventDescriptor(processIdentifier: app.processIdentifier)
guard AEDeterminePermissionToAutomateTarget(target.aeDesc, AEEventClass(kAECoreSuite),
                                           AEEventID(kAEGetData), false) == noErr else { refuse() }
let request = NSAppleEventDescriptor(eventClass: AEEventClass(kAECoreSuite), eventID: AEEventID(kAEGetData),
    targetDescriptor: target, returnID: AEReturnID(kAutoGenerateReturnID), transactionID: AETransactionID(kAnyTransactionID))
// VoiceOver.sdef: read-only last phrase (lapr), its read-only content (lptx).
let phrase = property(0x6c617072, of: NSAppleEventDescriptor.null())
request.setParam(property(0x6c707478, of: phrase), forKeyword: AEKeyword(keyDirectObject))
do {
    let reply = try request.sendEvent(options: [.waitForReply, .neverInteract], timeout: 1.0)
    if let error = reply.paramDescriptor(forKeyword: AEKeyword(keyErrorNumber)), error.int32Value != 0 { refuse() }
    guard let value = reply.paramDescriptor(forKeyword: AEKeyword(keyDirectObject))?.stringValue,
          !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
          value.utf8.count <= 32768, !app.isTerminated,
          let current = NSRunningApplication(processIdentifier: app.processIdentifier),
          current.launchDate == launched, appleVoiceOver(current.processIdentifier) else { refuse() }
    // Never return the phrase, its digest, length, or native error messages.
    print("{\"schemaVersion\":1,\"status\":\"KNOWN\",\"captureResponsive\":true}")
} catch { refuse() }
