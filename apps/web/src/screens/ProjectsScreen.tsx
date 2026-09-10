/**
 * The authorized projects, and adding one.
 *
 * The form carries the module's first rule: **entering a URL is not permission to test it.** A
 * repository may only be recorded together with the person who authorized it, the server refuses
 * the pair otherwise, and the form says so above the field rather than in a tooltip. UI-UX section
 * 2 is explicit that required explanatory text cannot live only in a tooltip.
 *
 * Creation is a mutation with no idempotency key, deliberately. A key would make a double
 * submission return the first project instead of creating a second, which sounds safer and is
 * wrong: two presses of "Add project" by a person who meant it are two projects, and the server has
 * no way to tell that from a retry. Retries are the client's tool where the client knows it is
 * retrying; this form does not retry.
 */

import { useId, useState } from 'react'
import { Link } from 'react-router-dom'
import type { JSX } from 'react'

import { RouteHeading } from '../a11y/RouteHeading'
import { useAnnouncer } from '../a11y/Announcer'
import { Button } from '../components/Button'
import { DataTable } from '../components/DataTable'
import { ErrorSummary } from '../components/ErrorSummary'
import { FormField } from '../components/FormField'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { EmptyState } from '../components/states'
import { createProject, listMembers, listProjects } from '../api/resources'
import type { Project } from '../api/resources'
import { useResource } from '../api/useResource'
import { workspacePath } from '../routes/routeMap'
import { useSession } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'

export const ProjectsScreen = (): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const { client } = useSession()
  const { announce } = useAnnouncer()
  const projects = useResource(
    (signal) => listProjects(client, workspaceId, signal),
    [client, workspaceId],
  )
  // The authorizing person is recorded by identity, not by name. A free-text field here asked for
  // something the server cannot store and produced an unhandled error when a real person typed
  // their name into it; a picker of actual members asks for the thing the record needs.
  const members = useResource(
    (signal) => listMembers(client, workspaceId, signal),
    [client, workspaceId],
  )

  const [name, setName] = useState('')
  const [repositoryUrl, setRepositoryUrl] = useState('')
  const [authorizedBy, setAuthorizedBy] = useState('')
  const [errors, setErrors] = useState<readonly { fieldId: string; message: string }[]>([])
  const [submissionId, setSubmissionId] = useState(0)
  const [busy, setBusy] = useState(false)
  // Generated here rather than read back out of the fields, so the error summary can link to a
  // control before that control has rendered. Writing to state during render to discover an id is
  // how a form ends up re-rendering itself in a loop.
  const nameFieldId = useId()
  const repositoryFieldId = useId()
  const authorizedByFieldId = useId()

  const submit = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    setSubmissionId((current) => current + 1)

    const found: { fieldId: string; message: string }[] = []
    if (name.trim() === '') {
      found.push({ fieldId: nameFieldId, message: 'A project needs a name.' })
    }
    if (repositoryUrl.trim() !== '' && authorizedBy.trim() === '') {
      found.push({
        fieldId: authorizedByFieldId,
        message:
          'Name the person who authorized testing this repository. Reachability is not consent, ' +
          'so a repository cannot be recorded without one.',
      })
    }
    setErrors(found)
    if (found.length > 0) return

    setBusy(true)
    const outcome = await createProject(client, workspaceId, {
      name: name.trim(),
      ...(repositoryUrl.trim() === '' ? {} : { repositoryUrl: repositoryUrl.trim() }),
      ...(authorizedBy.trim() === '' ? {} : { repositoryAuthorizedBy: authorizedBy.trim() }),
    })
    setBusy(false)

    switch (outcome.kind) {
      case 'ok':
      case 'accepted':
        setName('')
        setRepositoryUrl('')
        setAuthorizedBy('')
        announce(`Project ${name.trim()} added.`)
        projects.reload()
        break
      case 'problem':
        setErrors([{ fieldId: nameFieldId, message: outcome.problem.detail }])
        break
      case 'offline':
        setErrors([
          {
            fieldId: nameFieldId,
            message:
              'The request did not reach the server, so nothing was created, and pressing Add ' +
              'project again will send it once.',
          },
        ])
        break
      case 'cancelled':
      case 'stale':
      case 'unauthenticated':
        break
    }
  }

  return (
    <>
      <RouteHeading>Projects</RouteHeading>

      <ResourceView resource={projects} what="the projects in this workspace">
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState heading="No project is configured" because="nothing-created-yet">
              <p className="af-secondary">
                A project records what somebody authorized: a repository at an exact identity, and
                the environments a journey may reach.
              </p>
            </EmptyState>
          ) : (
            <DataTable<Project>
              caption="Projects in this workspace"
              rows={page.items}
              rowKey={(project) => project.projectId}
              columns={[
                {
                  key: 'name',
                  header: 'Project',
                  isRowHeader: true,
                  cell: (project) => (
                    <Link
                      className="af-link"
                      to={workspacePath(workspaceId, `projects/${project.projectId}`)}
                    >
                      {project.name}
                    </Link>
                  ),
                },
                {
                  key: 'repository',
                  header: 'Repository',
                  cell: (project) =>
                    project.repositoryUrl === null ? (
                      <span className="af-secondary">Not recorded</span>
                    ) : (
                      <code>{project.repositoryUrl}</code>
                    ),
                },
                {
                  key: 'created',
                  header: 'Added',
                  cell: (project) => <time dateTime={project.createdAt}>{project.createdAt}</time>,
                },
              ]}
            />
          )
        }
      </ResourceView>

      <section className="af-panel af-stack">
        <h2>Add a project</h2>
        <Notice tone="information" heading="Recording a repository is an authorization" headingLevel={3}>
          <p>
            A repository being reachable is not permission to automate against it. Naming the person
            who authorized it is what makes that permission auditable afterwards, so the server
            refuses a repository without one.
          </p>
        </Notice>

        <ErrorSummary submissionId={submissionId} errors={errors} />

        <form onSubmit={(event) => void submit(event)} noValidate>
          <FormField id={nameFieldId} label="Project name" required>
            {({ id, describedBy, invalid }) => (
              <input
                id={id}
                value={name}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
                onChange={(event) => setName(event.target.value)}
              />
            )}
          </FormField>

          <FormField
            id={repositoryFieldId}
            label="Repository URL"
            hint="Optional. Recorded as the exact identity a build will be taken from."
          >
            {({ id, describedBy }) => (
              <input
                id={id}
                value={repositoryUrl}
                aria-describedby={describedBy}
                onChange={(event) => setRepositoryUrl(event.target.value)}
              />
            )}
          </FormField>

          <FormField
            id={authorizedByFieldId}
            label="Authorized by"
            hint="The member of this workspace who authorized testing this repository. Required whenever one is given."
          >
            {({ id, describedBy, invalid }) => (
              <select
                id={id}
                value={authorizedBy}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
                onChange={(event) => setAuthorizedBy(event.target.value)}
              >
                <option value="">
                  {/* The empty option says why it is the only one. A picker with nothing in it and
                      no explanation leaves a person unable to satisfy a rule the form insists on,
                      with the error pointing at a control that has no options. */}
                  {members.state.kind === 'ready' ? 'Nobody selected' : 'Members could not be read'}
                </option>
                {members.state.kind === 'ready' &&
                  members.state.value.items.map((member) => (
                    <option key={member.userId} value={member.userId}>
                      {member.email} ({member.role.toLowerCase()})
                    </option>
                  ))}
              </select>
            )}
          </FormField>

          {members.state.kind !== 'ready' && members.state.kind !== 'loading' && (
            <Notice tone="warning" heading="The member list could not be read" headingLevel={3} live>
              <p>
                A repository cannot be recorded without naming the person who authorized it, and
                that name comes from this workspace's membership. Add the project without a
                repository, or try again once the list loads.
              </p>
            </Notice>
          )}

          <Button type="submit" variant="primary" busy={busy}>
            Add project
          </Button>
        </form>
      </section>
    </>
  )
}
