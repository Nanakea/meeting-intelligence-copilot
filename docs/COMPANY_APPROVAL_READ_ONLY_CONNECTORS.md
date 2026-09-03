# Company Approval: Read-Only Meeting and Knowledge Connectors

This document is the approval boundary for meeting capture and every enterprise source exposed by
the API v13 candidate. The product reads selected company sources to provide cited context. It
cannot change or delete remote company content.
Disconnect and retention operations delete only the current Windows user's encrypted local cache,
credentials, and selections.

## Requested permissions

| System | Approval and permissions required | Explicitly not requested |
|---|---|---|
| Zoom | Approval to capture meeting audio locally through the desktop recorder; microphone and Windows system-audio permissions; company recording/transcription policy approval; participant notification or consent required by company policy | No Zoom OAuth app, API token, meeting-management permission, cloud-recording access, chat access, user-directory access, or Zoom write/delete permission |
| Notion | Workspace owner approval for a Selected Workspaces/internal connection; **Read content** capability only; **No user information**; manually share only approved pages with the connection; allow outbound HTTPS to `api.notion.com:443` | No Update content, Insert content, Read/Insert comments, user/email access, workspace-wide PAT, file upload, page creation, page update, archive, or delete capability |
| Slack | Workspace administrator approval for an internal or directory-published Real-time Search app; user scopes `search:read.public` and, only if approved, `search:read.private`; explicitly selected public/private channels; allow outbound HTTPS to `slack.com:443`; register `meeting-intelligence://oauth/slack` | No `chat:write`, `chat:write.public`, `search:read.im`, `search:read.mpim`, `search:read.files`, channel-management, member-management, reactions, message update, message delete, bot posting, or ordinary DM access |
| SharePoint / OneDrive | Entra public-client registration, delegated `User.Read` and `Files.Read`, signed-in-user OneDrive, and explicitly selected SharePoint drives | No `Files.ReadWrite`, `Sites.ReadWrite.All`, tenant-wide application access, sharing, upload, update, or delete |
| Microsoft Teams context | Administrator-approved resource-specific consent or delegated read permission for explicitly selected teams/channels and selected meeting chats | No `ChannelMessage.Send`, read/write message scopes, ordinary DMs, membership changes, app installation management, update, or delete |
| GitHub | Organization-owned GitHub App installed on explicitly selected repositories with repository Metadata, Contents, Issues, Pull requests, Checks, Actions, and Deployments set to **Read-only** only where required | No Administration, Members, Secrets, Workflows write, Contents write, Issues write, Pull requests write, repository deletion, or all-repository installation by default |
| GitLab | OAuth application limited to `read_api` and `read_repository`, selected groups/projects, and pinned self-managed host if applicable | No `api`, `write_repository`, runner administration, project/group administration, issue/MR mutation, or repository deletion |
| NetSuite | Dedicated integration record, certificate-based OAuth client credentials, dedicated least-privilege role, approved metadata catalog, bounded SuiteQL entities/fields, subsidiary/environment restrictions | No generic record write permission; no customer, vendor, order, inventory, accounting, configuration, PATCH, POST, or DELETE operations |
| WMS | Dedicated read-only API identity, one approved HTTPS origin, allow-listed entity endpoints/fields, environment, rate limits, and business-key mappings | No generic endpoint access, inventory adjustment, wave/pick/pack/ship confirmation, order update, master-data change, or DELETE |
| Amazon FBA | Approved Selling Partner API application/role for only the inventory and inbound read operations used by configured marketplaces; LWA credential approval | No inbound-plan creation/cancellation, listing/order mutation, fulfillment action, report scope beyond configured reads, restricted-data access, or DELETE |
| Jira / Confluence / Azure DevOps / ServiceNow | Read-only OAuth/Entra/API scopes for explicitly selected sites, projects, repositories, work items, pages, tables, and services | No issue/work-item/change/page creation, edit, transition, comment, build execution, release execution, or delete |
| SQL / SFTP / OpenAPI | Approved read-only SQL views and parameter templates; SFTP read-only account plus pinned host key and roots; validated OpenAPI spec with allow-listed GET/HEAD operations | No arbitrary SQL, stored procedures, filesystem write/rename/delete, unpinned hosts, redirects, POST/PUT/PATCH/DELETE, or credential forwarding |

Notion connections require content to be shared with the connection and support a read-only content
capability. See [Notion authorization](https://developers.notion.com/guides/get-started/authorization)
and [Notion capabilities](https://developers.notion.com/reference/capabilities). Slack's search scopes
are described in the [Slack Data Access API documentation](https://api.slack.com/docs/apps/data-access-api).

## Implemented enforcement

- Zoom is captured through the desktop's local audio/STT path. The application does not authenticate to
  Zoom and cannot call Zoom APIs.
- Notion uses a narrow reader that can only issue `GET` for markdown on explicitly enrolled page
  UUIDs. Raw page IDs are DPAPI-encrypted and citations show only the approved local page label.
- Notion and Slack results are live-only. They are discarded after retrieval and are not retained as
  learning examples or placed in `MeetingState`.
- Slack authorization requests only public/private search scopes. The code contains no Slack bot
  credential endpoint and no `chat.postMessage` executor.
- Teams, Slack, repository, issue-tracker, ServiceNow, and NetSuite outbound executors are not
  composed. Historical action workflows create reviewed exports only; approval returns
  `direct_writes_disabled`.
- Provider credentials are stored in Windows Credential Manager. Provider IDs, local configuration,
  and indexes are protected with DPAPI for the current Windows user.
- Fixed HTTPS origins, disabled redirects, response-size bounds, timeouts, source allowlists, and
  principal checks reduce SSRF, redirect, resource-exhaustion, and cross-source disclosure risk.
- External content is untrusted. It cannot become transcript evidence, fill a meeting gap, confirm a
  governance record, alter an authority policy, or execute instructions found inside documents.
- Logs and UI omit tokens, raw provider IDs, transcript text, raw exceptions, SQL, and development
  paths.

## Company decisions required

1. Name the business owner, security reviewer, privacy reviewer, and support owner.
2. Approve which meeting types may be captured and the participant notification/consent wording.
3. Approve retention for recordings, transcripts, encrypted local indexes, and aggregate diagnostics.
4. Approve the exact Notion pages, SharePoint drives, repositories/projects, ERP entities/fields,
   WMS endpoints, Amazon marketplaces, and Slack/Teams spaces. Do not approve an entire tenant by
   default.
5. Confirm whether private Slack-channel search is allowed. If not, grant only
   `search:read.public`.
6. Approve installation of the unsigned/signed Windows pilot and its loopback sidecar behavior.
7. Confirm device requirements: managed Windows account, disk encryption/BitLocker, endpoint
   protection, screen lock, and least-privilege user access.
8. Run non-production acceptance with test content before any production source is selected.

## Security acceptance tests

- Verify Notion cannot create, update, archive, comment on, or delete a page.
- Verify Slack cannot post, update, or delete a message and cannot read DMs, group DMs, or files.
- Revoke a Notion page and Slack channel, then confirm retrieval immediately stops.
- Disconnect each connector and confirm its Credential Manager secret and encrypted selections are
  removed without changing the remote source.
- Search for a known unauthorized record and require zero citations.
- Inspect aggregate diagnostics and exports for token-shaped strings, provider IDs, transcript text,
  meeting titles, personal names, and local paths.
- Test offline, throttled, expired-token, malformed-response, and oversized-response cases; recording
  and ASK NOW must continue in meeting-only mode.

## Residual risks

No software can eliminate all risk. Approval should explicitly accept the following bounded risks:

- A compromised Windows user session can read whatever that user can display and can control the
  local application. Device hardening and BitLocker are required compensating controls.
- Audio capture may collect confidential or personal information. Meeting policy, consent, retention,
  and deletion procedures remain organizational responsibilities.
- Incorrectly sharing a broad Notion parent page or approving private Slack search can expose more
  content than intended. Source selection must be reviewed periodically.
- Slack's private-search OAuth scope is workspace/user scoped rather than a per-channel grant. The
  adapter discards every result outside the locally selected channels, but Slack may search the
  broader content authorized to that user. If company policy requires provider-side per-channel
  enforcement, do not approve `search:read.private`; use public-only search or leave Slack disabled.
- Provider APIs and permission semantics can change. Re-run scope and revocation acceptance after
  connector or provider upgrades.
- Zoom support means local capture of Zoom audio, not a certified Zoom Marketplace integration.

## GitHub repository ownership handoff

Prefer a GitHub repository transfer because it preserves commits, issues, pull requests, releases,
settings, and redirects. The receiving account must not already own a repository or fork with the
same name, and a personal-account transfer must be accepted within one day.

1. Make the target account or organization ready and enable 2FA. For an organization, confirm you
   can create repositories there and that private-repository policy is correct.
2. Record the current commit and remote before transfer:

   ```powershell
   git status --short --branch
   git rev-parse HEAD
   git remote -v
   gh auth status
   ```

3. In GitHub, open the repository, then **Settings > General > Danger Zone > Transfer**. Enter the
   receiving username/organization and repository name. The receiving personal account must accept
   the email promptly. See [GitHub's transfer procedure](https://docs.github.com/en/repositories/creating-and-managing-repositories/transferring-a-repository).
4. Sign out only after the transfer is accepted. GitHub CLI logout removes local CLI authentication
   but does not revoke the token at GitHub:

   ```powershell
   gh auth logout --hostname github.com --user OLD_ACCOUNT
   gh auth login --hostname github.com --web --git-protocol https
   gh auth setup-git --hostname github.com
   gh auth status
   ```

   To revoke the old GitHub CLI authorization entirely, revoke the GitHub CLI OAuth application in
   the old account's application settings. See [`gh auth logout`](https://cli.github.com/manual/gh_auth_logout)
   and [`gh auth login`](https://cli.github.com/manual/gh_auth_login).
5. Update the local remote even though GitHub normally redirects the old URL:

   ```powershell
   git remote set-url origin https://github.com/NEW_OWNER/meeting-intelligence-copilot.git
   git remote -v
   git fetch origin
   git push --dry-run origin HEAD:main
   ```

6. Recreate or review Actions secrets, environments, rulesets, branch protection, deploy keys,
   GitHub Apps, package permissions, security alerts, and billing. Secrets are not values that should
   be copied through Git history.
7. Confirm the transferred private repository's visibility, default branch, current commit, and
   Windows CI result before making a real push.

If company policy does not allow transfer, create an empty private repository under the approved
owner and mirror Git refs. This preserves Git history but not issues, pull requests, settings, or
secrets, so it is the second-choice method.
