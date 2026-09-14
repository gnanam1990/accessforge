/** Private measurement envelope. Never part of the navigator's reader-text projection. */
export type KeyboardFocusRecord = Readonly<{
  measurementKind: 'AX_KEYBOARD_FOCUS'; capturedAtUtc: string;
} & ({ status: 'KNOWN'; role: string; identifierDigest: string } |
     { status: 'UNKNOWN'; reason: 'NATIVE_FOCUS_UNAVAILABLE' })>;

export function parseKeyboardFocus(value: unknown): KeyboardFocusRecord {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error('keyboard focus unavailable');
  const row = value as Record<string, unknown>;
  const keys = row.status === 'KNOWN' ? 'capturedAtUtc,identifierDigest,measurementKind,role,status'
    : 'capturedAtUtc,measurementKind,reason,status';
  if (Object.keys(row).sort().join(',') !== keys || row.measurementKind !== 'AX_KEYBOARD_FOCUS' ||
      (row.status !== 'KNOWN' && row.status !== 'UNKNOWN') || typeof row.capturedAtUtc !== 'string' ||
      row.capturedAtUtc.length > 40 || !Number.isFinite(Date.parse(row.capturedAtUtc)) ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(row.capturedAtUtc)) throw new Error('keyboard focus malformed');
  if (row.status === 'KNOWN') {
    if (typeof row.role !== 'string' || !['AXTextField', 'AXTextArea', 'AXButton', 'AXCheckBox',
      'AXRadioButton', 'AXPopUpButton', 'AXComboBox', 'AXLink'].includes(row.role) ||
      typeof row.identifierDigest !== 'string' || !/^[a-f0-9]{64}$/.test(row.identifierDigest)) throw new Error('keyboard focus target malformed');
    return Object.freeze({ measurementKind: 'AX_KEYBOARD_FOCUS', status: 'KNOWN', capturedAtUtc: row.capturedAtUtc,
      role: row.role, identifierDigest: row.identifierDigest });
  }
  if (row.reason !== 'NATIVE_FOCUS_UNAVAILABLE') throw new Error('keyboard focus reason malformed');
  return Object.freeze({ measurementKind: 'AX_KEYBOARD_FOCUS', status: 'UNKNOWN', capturedAtUtc: row.capturedAtUtc,
    reason: 'NATIVE_FOCUS_UNAVAILABLE' });
}
