-- Module 30, gap found by the first real-browser run: a non-member tester or
-- client viewer could not reach the tester-desk UI at all.
--
-- `GET /v1/session` answers "which workspaces you may enter" from
-- `workspace_membership` alone, and WorkspaceRoute renders NotFoundState when
-- the workspace is absent from that answer. Tester-desk participants are
-- deliberately not members — tester authority is the td_tester row, client
-- read access is the td_client_viewer grant — so their workspaces never
-- appeared in the session and their own desks were unreachable in the app.
--
-- `user_connection` still sees zero rows: both tables carry a workspace-scoped
-- policy. Widening that policy would be wrong twice over. It governs every
-- command — a caller could then DELETE their own row because DELETE consults
-- only USING, and WITH CHECK does not constrain it. So the workspace policy is
-- left exactly as written and a second, permissive, SELECT-only policy sits
-- beside it: a signed-in caller may additionally *read* the rows that are
-- their own. Writes stay scoped to the workspace, always — enrollment, grants
-- and revocation still go through the workspace that owns them.

CREATE POLICY own_row_select ON td_tester
    FOR SELECT
    USING (current_app_user_id() IS NOT NULL AND user_id = current_app_user_id());

CREATE POLICY own_row_select ON td_client_viewer
    FOR SELECT
    USING (current_app_user_id() IS NOT NULL AND user_id = current_app_user_id());
