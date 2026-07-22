# Telegram notification channel

This integration adds `Telegram` to the standard Frappe v16 `Notification` DocType. It deliberately does not install the legacy app's guest send/PDF endpoints, plaintext token fields, global document hooks or synchronous provider calls.

## Configure a bot

1. Create a dedicated Telegram bot for ERPNext notifications. Keep the customer-identification webhook bot separate unless the same trust boundary and lifecycle are explicitly approved.
2. In Desk, create `Telegram Bot Profile`, enter a clear profile name and the username without `@`, then paste the token into `Bot Token`.
3. Enable and save the profile. Only System Manager can read/write the permlevel-1 Password field; Sales Manager has profile read access without the token.
4. Open the saved profile and use `Send Test Message` with a controlled numeric chat ID.

The API host is fixed to `https://api.telegram.org`. Responses are bounded, redirects and retries are disabled, and no setting can redirect a bot token to another host.

## Configure recipients

Each standard `Notification Recipient` row can use one or more of:

- `Telegram Chat ID`: a direct numeric private, group or channel chat ID;
- `Receiver By Document Field`: `owner` or a Link to User, Customer, Employee or Supplier, including links in child tables;
- `Receiver By Role`: enabled Users assigned to the role;
- `Send To All Assignees`: Users assigned to the reference document.

For linked or role-based delivery, populate `ua_telegram_chat_id` on the relevant User, Customer, Employee or Supplier. Only numeric IDs up to 20 digits, optionally negative, are accepted. A bot must already be allowed to message the private chat or be a member of the target group/channel.

## Create a notification

1. Create the usual Frappe `Notification` and select `Telegram` as Channel.
2. Select an enabled `Telegram Bot Profile`.
3. Configure the event, conditions and recipient rows using the normal Frappe v16 workflow.
4. Enter a Jinja subject and message. Rendered HTML tags are removed before Telegram delivery.
5. Optionally enable `Attach Print` and select a Print Format. The rendered message becomes the PDF caption and therefore must not exceed 1024 characters.

Text messages are limited by the selected profile, with an absolute ceiling of 4096 characters. File-field and all-file attachments are intentionally rejected. A print PDF is rendered in memory and uploaded to Telegram through `sendDocument`; ERPNext does not publish a guest URL.

## Runtime and audit behavior

Notification evaluation only queues delivery after the database transaction commits. Each chat receives a separate deduplicated job and one Bot API side effect. The operation is marked `unknown` immediately before the request:

- an explicit non-transient 4xx rejection becomes `failed`;
- a timeout, 408, 429, 5xx, malformed response or success without `message_id` remains `unknown`;
- a confirmed response with `message_id` becomes `succeeded`.

Never resend an `unknown` operation blindly. Reconcile it in Telegram/provider evidence and `UA Integration Operation`, then use the existing manual resolution workflow. Successful document notifications add a masked `Communication` timeline entry; full chat IDs and raw provider responses are not stored there.

## Manual API

Authorized Sales users can integrate a custom form action with the POST-only method:

```text
ukrainian_integrations.communication.telegram.service.enqueue_telegram_message
```

Required arguments are `bot_profile`, numeric `chat_id`, `message` and a unique `idempotency_key`. Optional reference and print arguments are checked against the caller's document/print permissions. The method queues work and never returns or accepts a bot token.

## Deploy or upgrade

After taking a backup and deploying the same commit to every process, run:

```bash
bench --site <site> migrate
bench build --app ukrainian_integrations
bench --site <site> execute ukrainian_integrations.diagnostics.run_installation_checks
```

Restart web, scheduler and all workers. Then complete the Telegram section in `docs/PROVIDER_ACCEPTANCE.md` before enabling live notifications.
