# Google Chat notifications and parent attachments

## Google Chat setup

### Space Webhook Delivery (Selected Configuration)

The selected workflow as of 2026-10-14 is one task-created message to the
`タスク管理システム通知` space, mentioning assigned people other than the creator. It is not a
bot-to-user DM workflow. No Marketplace listing, store screenshot, private
policy-page publication, Apps Script receiver, or individual bot installation
is needed for an incoming webhook.

```dotenv
GOOGLE_CHAT_AUTH_MODE=webhook
GOOGLE_CHAT_SPACE_ID=AAQAp2U8464
GOOGLE_CHAT_WEBHOOK_CONFIG_FILE=D:\task\approval_connections\routine_app_space_webhook.dat
GOOGLE_CHAT_DIRECTORY_TOKEN_FILE=D:\task\approval_connections\routine_app_bot_source_token.dat
```

The task webhook is encrypted with machine DPAPI, outside the web root, with
the same restricted IIS reader ACL as the existing task authorization file.
The plaintext webhook URL is not retained in `.env`. `GOOGLE_CHAT_SPACE_ID`
must match the webhook's space; unconfirmed/mismatched targets are rejected.
The approval notification webhook/configuration is not read as a fallback or
modified. No failure in space mode falls back to DM delivery.

The existing directory authorization resolves assignee emails to numeric
Google user IDs. The message prefixes all resolved `<users/{id}>` mentions,
deduplicates recipients, and posts only once. Task text cannot inject `@all`
or arbitrary mention markup. A stable per-task message ID is supplied to
prevent duplicate message creation. There is no automatic retry queue.
Unresolved assignees are reported separately from a failed space post; neither
failure undoes task creation. Users need appropriate access to the destination
space. A generated mention alone is not proof that a nonmember receives a
notification; webhook credentials cannot inspect membership or notification
preferences.

As of 2026-10-15, the creation route excludes the authenticated creator by
employee name and resolved email (including the sign-in UPN), using trimmed,
case-insensitive comparisons. The stored assignee list and notification task
details remain unchanged. A creates A+B: mention B only. A creates B+C: mention
B+C. A creates A only: do not call the notification sender or post to the space;
return `status=skipped`, `reason=no_other_assignees` without a UI warning.
The sender also refuses an empty recipient list. If other assignees exist but
cannot be resolved, this remains a notification failure, not a successful skip.

Incoming webhook responses contain message/thread identifiers, not the full
message text or mention annotations. Confirm visible Japanese text and mention
rendering in the target space after the explicitly labeled connection test.
Do not claim readback verification solely from HTTP success.

On 2026-10-14, at the user's request, one explicitly labeled test notification
was posted to this space, mentioning only `m-mori@sourcenext.com`. The production
notification function was used with synthetic in-memory task details, without
database creation or edits. The request returned HTTP 200 and a message name
under `spaces/AAQAp2U8464`; only one POST was made. The submitted UTF-8 payload
was checked. The user's subsequent Chat screenshot confirmed the rendered
mention of Mori and correct Japanese text, including the explicit test label.
This verifies one recipient; multi-assignee composition is covered by the
automated tests, not a live message mentioning colleagues. No unsupported
message-read permission was added. IIS `routine_app`
was restarted with space mode selected, and the protected webhook configuration
was successfully loaded. No plaintext webhook remains in `.env`.

The Marketplace image files and support-page draft below were prepared under
the previous DM rollout assumption; they are not required by this workflow.

### Keyless Bot Delivery (Legacy DM Configuration)

Organization policy blocks service account key creation. Keep that policy in
place. Use service account impersonation to mint a five-minute `chat.bot` access
token for `sys-task-notice@sys-task-notice.iam.gserviceaccount.com`; no private key is created
or exported. The Google user performs directory lookup and IAM token creation,
while the Chat service account is the message sender. The existing `sys-notice`
project hosts the separate approval-notification app. Do not rename or modify
that Chat app. Each Chat app requires its own project; use `sys-task-notice`
for the routine-task bot. The existing OAuth client is retained only for IAM
token creation and directory lookup, not as the message sender.

1. Enable the IAM API (`iam.googleapis.com`), IAM Service Account Credentials API
   (`iamcredentials.googleapis.com`), and Google Chat API (`chat.googleapis.com`)
   in project `sys-task-notice`. IAM token creation currently uses the existing
   OAuth client's `sys-notice` consumer project, where IAM Credentials is already
   enabled. IAM administration requests use `sys-task-notice` as the quota project.
2. Grant the authorizing Google user `roles/iam.serviceAccountTokenCreator` on
   this service account only. This requires `iam.serviceAccounts.getAccessToken`;
   the service-account User role (`actAs`) alone is not sufficient.
3. Authorize the existing OAuth client once with `iam` and `directory.readonly`:

```powershell
& 'D:\asp\routine_app\.venv\Scripts\python.exe' 'D:\asp\routine_app\authorize_google_chat.py' --reader 'IIS APPPOOL\routine_app' --keyless-bot
```

For an authorized workstation, add `--remote --no-browser`. The user OAuth
refresh token is encrypted on Mercury with machine DPAPI and the existing
restricted IIS reader ACL. It is not a service account key. No per-recipient
OAuth consent or domain-wide delegation is needed.

4. Activate these settings (already prepared in `.env`):

```dotenv
GOOGLE_CHAT_AUTH_MODE=bot
GOOGLE_CHAT_BOT_SERVICE_ACCOUNT=sys-task-notice@sys-task-notice.iam.gserviceaccount.com
GOOGLE_CHAT_BOT_SOURCE_TOKEN_FILE=D:\task\approval_connections\routine_app_bot_source_token.dat
GOOGLE_CHAT_DIRECTORY_TOKEN_FILE=D:\task\approval_connections\routine_app_bot_source_token.dat
```

5. Configure the Chat app in the same Google Cloud project and install it for
   recipients as described below. Use the display name `ルーチンタスク通知`.
   Bot-to-user DMs must exist before delivery. As of 2026-10-14, task 321 was
   successfully delivered to `m-mori@sourcenext.com`; the fetched message confirmed
   sender type `BOT`, the configured display name, and correct Japanese text.
   An unconfigured app is reported as `bot_not_configured`, separately from
   `bot_not_installed` for a missing recipient DM.

### Bot Avatar

`chat_bot_icon.png` is a 512x512 transparent PNG rendered directly from the
`link[rel="icon"]` data URI in `index.html`. It preserves the existing red glyph
and font styling; the unrelated standalone `favicon.svg` was not used.
The PNG is the only file approved for public image sharing. It contains no task
or employee data. Use an externally readable HTTPS image URL for the Chat API
Avatar URL; an internal Mercury URL is not a verified public image host.
The image was uploaded to Drive as `routine-task-notice-icon.png`. The user
approved public read access for this image only. Anonymous HTTPS retrieval was
verified on 2026-10-14: HTTP 200, `image/png`, 14,438 bytes, and the same SHA-256
as the local PNG. Avatar URL:

```text
https://drive.google.com/uc?export=view&id=1Ggb44mWsohx2edPVB4IOkJhX4i5HvBBS
```

Change only the Avatar URL in `sys-task-notice`; leave the approval app and the
Chat event handler configuration untouched. After the user saved the setting
on 2026-10-14, the Chat API returned a new Google-hosted proxy URL for the bot's
avatar. That proxy returned a 512x512 PNG whose decoded RGBA pixels exactly
matched the exported tab icon. Removing the Drive file's public read access
will make the configured image URL unsuitable for the bot.

### Marketplace Listing Assets (Draft)

Prepared on 2026-10-14 for the private organization listing:

| File | Size | Listing field |
| --- | --- | --- |
| `chat_bot_icon_32.png` | 32x32 | Application icon |
| `chat_bot_icon_128.png` | 128x128 | Application icon |
| `chat_bot_banner.png` | 220x140 | Application card banner |

The icons are direct browser renders of the existing inline tab SVG. The banner
uses that same glyph and the app name. Dimensions, visible red pixels, and
banner text fit were verified. These new files have not been uploaded or shared
publicly. A real notification screenshot without confidential task or employee
details still needs to be selected for the listing.

`google_chat_listing_support_draft.md` contains proposed support-page text.
It is not an approved policy or a published URL. Confirm the listed open items
before hosting it and entering Marketplace support links. The production
deployment ID was supplied and saved by the user, but its event handler source
and callback execution have not been independently inspected or tested.

### Recipient Mentions

Each bot DM starts with `<users/{numeric_user_id}>` for that DM's recipient.
The ID is resolved by the existing directory lookup, not a plain-text `@name`
or a guessed email alias. Task details and the full assignee list are preserved.
Multiple assignees receive separate DMs with their own mentions. The stable
per-task message ID and recipient deduplication remain unchanged.

Verified on 2026-10-14 with one explicitly labeled task-321 test DM to
`m-mori@sourcenext.com` only. Google returned sender type `BOT` and a
`USER_MENTION` annotation matching the recipient's numeric user ID. Japanese
text was preserved. No task row was created or edited for this verification.

For search and manual installation before Marketplace publication, enable
interactive features, configure the dedicated Apps Script deployment, and add
the recipient's Google account or group to Visibility. An API-only app with
interactive features disabled isn't sufficient for this installation workflow.
The temporary Head deployment is for testing; switch to a versioned deployment
before rolling out to all assignees. Do not use a fabricated HTTP trigger URL or
an internal Mercury page URL as the inbound event handler.

- [User mentions](https://developers.google.com/workspace/chat/format-messages#mention-users)
- [Test and install an unpublished app](https://developers.google.com/workspace/chat/test-interactive-features)

References:

- [Service account impersonation](https://cloud.google.com/docs/authentication/use-service-account-impersonation)
- [Generate a short-lived access token](https://cloud.google.com/iam/docs/reference/credentials/rest/v1/projects.serviceAccounts/generateAccessToken)

### Earlier Key-Based Bot Setup (Blocked by Organization Policy)

User-authenticated delivery cannot DM the authorizing account itself. Task 321
confirmed this with Google's `INVALID_ARGUMENT` response. Bot delivery sends as
the Chat app instead, including to the person who authorized directory lookup.

The existing OAuth client belongs to Google Cloud project `sys-notice`.

1. In that project's Google Chat API configuration, configure a notification-only
   Chat app with a name, icon and description. Configure its visibility/install
   settings for the target organization. Use a service account in the same project
   with `chat.bot`; no broad Editor/Owner role is needed for sending Chat messages.
2. Obtain its service account JSON key and keep it outside the web root. The helper
   below encrypts it with Windows machine DPAPI on Mercury and restricts the file
   ACL to the operator, SYSTEM, Administrators, and `IIS APPPOOL\routine_app`.
   It refuses to overwrite existing keys. Replace the source path with the actual
   downloaded key path; do not paste the JSON contents into chat or source code.

```powershell
& 'D:\asp\routine_app\.venv\Scripts\python.exe' 'D:\asp\routine_app\authorize_google_chat.py' --reader 'IIS APPPOOL\routine_app' --bot-key-file 'D:\secrets\google-chat-bot.json' --token-file 'D:\task\approval_connections\routine_app_chat_bot.dat'
```

On an authorized Windows workstation, add `--remote` to encrypt/store on Mercury
through WinRM without putting secrets in command arguments.

3. Authorize People API directory lookup once with the existing OAuth client.
   This identity only resolves email addresses to Google user IDs; it does not
   send messages. The approval system's existing token is Windows-user encrypted
   and could not be decrypted by the workstation or IIS/server administration
   identity, so a separate machine-encrypted token is required.

```powershell
& 'D:\asp\routine_app\.venv\Scripts\python.exe' 'D:\asp\routine_app\authorize_google_chat.py' --reader 'IIS APPPOOL\routine_app' --directory
```

From an authorized workstation, add `--remote --no-browser` for a local consent
link. No recipient needs to authorize OAuth.

4. Install the Chat app for recipients. A Workspace administrator can install it
   organization-wide to create app-to-user DMs without per-person installation.
   Otherwise, each recipient must add the bot once in Chat (not OAuth consent).
   `chat.bot` cannot create someone else's bot DM by itself; absent DMs are
   reported as `bot_not_installed`.
5. Activate the bot in the application's `.env` and recycle only its IIS pool:

```dotenv
GOOGLE_CHAT_AUTH_MODE=bot
GOOGLE_CHAT_BOT_CREDENTIALS_FILE=D:\task\approval_connections\routine_app_chat_bot.dat
GOOGLE_CHAT_DIRECTORY_TOKEN_FILE=D:\task\approval_connections\routine_app_directory_token.dat
```

Notification failures include `failures[].recipient` and `failures[].reason`.
The UI distinguishes a self DM from a missing bot installation; logs include
task ID, recipient, request stage and reason in `logs/google_chat.log`, with
rotation. Tokens, private keys, and raw credential-bearing errors aren't logged.

References:

- [Chat app authentication](https://developers.google.com/workspace/chat/authenticate-authorize-chat-app)
- [Find the bot's DM](https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces/findDirectMessage)
- [People directory lookup](https://developers.google.com/people/api/rest/v1/people/searchDirectoryPeople)

### Earlier User Delivery (Not Suitable for Self Notifications)

Creation sends one DM to every assigned person, including the registrant if
assigned. A group task sends to all members. Parent edits and routine extensions
do not send a creation notification. Existing Slack notifications are preserved.

The existing `\\mercury\task\approval_connections` integration uses a shared-space
webhook and a People API token with only `directory.readonly`. That token cannot
send DMs. This implementation reuses its installed-app OAuth client file,
`people_oauth_client.json`, but obtains a separate Chat token without changing
the approval integration's token, configuration, or notification behavior.

1. Enable Google Chat API in the existing OAuth client's Google Cloud project.
   Allow `chat.spaces.create` and `chat.messages.create` in the OAuth consent
   configuration and the organization's API access policy, if required.
2. On Mercury, install the updated requirements in the app's existing venv.

```powershell
& 'D:\asp\routine_app\.venv\Scripts\python.exe' -m pip install -r 'D:\asp\routine_app\requirements.txt'
```

3. On Mercury, run the following command interactively. Sign in with the Google
   account that should send the notifications, preferably a dedicated notification
   account. All DMs appear from that account. The command waits up to five minutes
   for browser consent. The verified IIS pool identity is `IIS APPPOOL\routine_app`.

```powershell
& 'D:\asp\routine_app\.venv\Scripts\python.exe' 'D:\asp\routine_app\authorize_google_chat.py' --reader 'IIS APPPOOL\routine_app'
```

Alternatively, run the helper from an authorized Windows workstation with
`--remote --no-browser`. It prints a consent link with a localhost callback on
that workstation, then sends the authorized token through encrypted WinRM stdin
for machine-DPAPI encryption and ACL-restricted storage on Mercury. Token data
is never placed in command arguments or logs.

The new token is stored at
`D:\task\approval_connections\routine_app_chat_token.dat` on Mercury (the verified
local path of the task share). `GOOGLE_CHAT_TOKEN_FILE` is configured in `.env`.
Windows machine
DPAPI encrypts it on Mercury; a protected file ACL grants access only to the
authorizing Windows user, SYSTEM, Administrators, and the specified IIS identity.
The command refuses to overwrite tokens. Reauthorization can use a new
`--token-file` path and `GOOGLE_CHAT_TOKEN_FILE` in `.env`. Neither plaintext
tokens nor client secrets are copied into the web root or committed.

4. Recycle the app's IIS pool through the normal deployment process. The default
   token path is already configured in `.env`. Optionally set the HTTPS public URL:

```dotenv
ROUTINE_PUBLIC_URL=https://mercury/routine_app
```

Recipient emails come from the Employee AD value; short AD names use the existing
`@sourcenext.com` suffix. The Chat API creates or reuses each recipient's 1:1 DM
using their email. There is no user-ID mapping or domain-wide delegation. Requests
have bounded timeouts. Notification errors never undo a successfully registered
task. The creation response includes `task_no` and `google_chat.status`
(`sent`, `partial`, `failed`, or `disabled`) and the UI displays the result.
Until first-time authorization, notifications remain `disabled`.
There is no automatic retry queue. A stable message ID prevents duplicate DMs
if the notification for the same task is retried.

References:

- [User authentication](https://developers.google.com/workspace/chat/authenticate-authorize-chat-user)
- [Create or reuse a DM](https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces/setup)
- [Send a message](https://developers.google.com/workspace/chat/create-messages)

## Attachments

Apply `db/migrations/20261013_add_parent_attachments.sql` to the routine database.
This additive, idempotent migration creates `dbo.routine_task_attachment` without
altering existing task data. Do not run `routine_tasks.sql` against an existing
database: that legacy initialization script drops task tables.

The parent creation/edit form accepts multiple files: 10 MB per file, 25 MB total,
and 10 files per request. Empty files are rejected. Task fields, child entries,
and new attachment bytes are saved in the same SQL transaction. Binary storage
uses the routine database and its existing backup process; it adds database size.

Authenticated users with the application's existing shared task access can list,
download, and remove attachments on active parents. Downloads are forced to
`application/octet-stream` with `nosniff` and `private, no-store`. Removing an
attachment soft-deletes its row. Completed/deleted parents' attachments are no
longer accessible. This follows the existing application-wide access policy;
there is no new per-person attachment access policy.

Endpoints (both `/api.py` and `/routine_app/api.py` prefixes):

- `GET /parent/{task_no}/attachments`: metadata only.
- `GET /parent/{task_no}/attachments/{attachment_id}`: download.
- `DELETE /parent/{task_no}/attachments/{attachment_id}`: remove.
- Task creation `POST /routines` and parent `PATCH /parent/{task_no}` accept
  either existing JSON or multipart form fields `data` (JSON object) and
  repeated `attachments` (files). Existing JSON clients continue to work.

## Verification

Run `python -m pytest test_task_features.py --rootdir=. --confcutdir=.` from
`tests/api` in a test venv (avoids the UNC `ai-rules` symbolic link).
Setting `ROUTINE_TEST_DATABASE=1` also runs the database lifecycle check inside
one rolled-back transaction. No Google messages are sent by the tests.
