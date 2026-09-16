import { expect, it } from 'vitest'
import { invitationQuery, invitationReference } from './invitationReference'

const reference = { workspaceId: '11111111-1111-4111-8111-111111111111', invitationId: '22222222-2222-4222-8222-222222222222' }
it('round trips exact invitation references, not redirect addresses', () => {
  const query = invitationQuery(reference)
  expect(invitationReference(`?${query}`)).toEqual(reference)
  expect(invitationReference(query + '&next=https://evil.test')).toBeNull()
  expect(invitationReference(query + '&invitationId=' + reference.invitationId)).toBeNull()
  expect(invitationReference('invitationWorkspace=' + reference.workspaceId)).toBeNull()
})
