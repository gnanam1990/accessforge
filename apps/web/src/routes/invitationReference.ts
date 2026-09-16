/** Untrusted navigation context only. No automatic read, acceptance or identity claim. */
export type InvitationReference = { readonly workspaceId: string; readonly invitationId: string }
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
export function invitationReference(search: string): InvitationReference | null {
  const params = new URLSearchParams(search)
  const workspaceId = params.get('invitationWorkspace') ?? ''
  const invitationId = params.get('invitationId') ?? ''
  if ([...params].length !== 2 || !UUID.test(workspaceId) || !UUID.test(invitationId)) return null
  return { workspaceId, invitationId }
}
export function invitationQuery(reference: InvitationReference): string {
  return new URLSearchParams({ invitationWorkspace: reference.workspaceId, invitationId: reference.invitationId }).toString()
}
