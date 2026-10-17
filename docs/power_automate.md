# Human review in Power Automate

When an investigation ends as `PENDING_APPROVAL` or `ESCALATED`, BatchGuard calls a Power Automate cloud flow. The flow sends a real approval request to the QA reviewer (email, Teams and the Approvals center), waits for the decision, and posts it back to `/investigations/{run_id}/approve` with the reviewer's name and comment. The decision lands in the append-only audit log.

```
BatchGuard /investigate ──POST──> Flow trigger ──> Start and wait for an approval ──> HTTP POST approve_url
      (status needs review)        (HTTP request)      (reviewer approves/rejects)       (X-Approval-Token)
```

## Payload BatchGuard sends

| Field | Example |
|---|---|
| run_id | `a21ca54a-...` |
| batch_id, reactor_id, status | `92`, `BR-102`, `PENDING_APPROVAL` |
| title | `BatchGuard: review deviation for batch 92` |
| details | deviations, probable cause, findings with record ids, CFR sections, actions |
| approve_url | `https://<public host>/investigations/<run_id>/approve` |
| details_url | `https://<public host>/investigations/<run_id>` |

## Build the flow (Developer environment)

1. **make.powerautomate.com** → environment picker → your Developer environment → **Create → Instant cloud flow → Skip** (or Automated) → add trigger **When an HTTP request is received** (Request connector).
2. In the trigger: **Who can trigger the flow: Anyone**. **Request Body JSON Schema**:

```json
{"type":"object","properties":{"run_id":{"type":"string"},"batch_id":{"type":"integer"},"reactor_id":{"type":"string"},"status":{"type":"string"},"title":{"type":"string"},"details":{"type":"string"},"approve_url":{"type":"string"},"details_url":{"type":"string"}}}
```

3. **+ New step → Approvals → Start and wait for an approval.**
   - Approval type: **Approve/Reject - First to respond**
   - Title: dynamic content **title**
   - Assigned to: your UTD email
   - Details: dynamic content **details**
   - Item link: dynamic content **details_url**. Item link description: `Open the investigation and audit trail`
4. **+ New step → HTTP** (premium, included in the Developer plan).
   - Method: `POST`
   - URI: dynamic content **approve_url**
   - Headers: `Content-Type` = `application/json`, `X-Approval-Token` = the same value as `APPROVAL_TOKEN`
   - Body: three expressions (use the **fx** expression editor for each value):

| Key | Expression |
|---|---|
| reviewer | `body('Start_and_wait_for_an_approval')?['responses']?[0]?['responder']?['displayName']` |
| decision | `if(equals(body('Start_and_wait_for_an_approval')?['outcome'], 'Approve'), 'APPROVED', 'REJECTED')` |
| comment | `if(empty(body('Start_and_wait_for_an_approval')?['responses']?[0]?['comments']), 'Decision recorded in Power Automate approvals without a comment', body('Start_and_wait_for_an_approval')?['responses']?[0]?['comments'])` |

   The Body field should end up as a JSON object `{"reviewer": ..., "decision": ..., "comment": ...}`.
5. **Save.** Copy the trigger's **HTTP URL**. It contains a signature, so treat it as a secret.

## Run it end to end

Terminal 1, a public tunnel to the local API (no account needed):

```bash
cloudflared tunnel --url http://localhost:8000
```

Terminal 2, the API with the flow wired in:

```bash
export POWER_AUTOMATE_FLOW_URL='<trigger HTTP URL>'
export PUBLIC_BASE_URL='https://<random>.trycloudflare.com'
export APPROVAL_TOKEN='<long random string>'
uvicorn api.main:app --port 8000
```

Terminal 3:

```bash
curl -s -X POST localhost:8000/investigate -H 'content-type: application/json' -d '{"batch_id": 92}'
```

Approve the request in email or at make.powerautomate.com → **Action items → Approvals**, then check the record:

```bash
curl -s localhost:8000/investigations/<run_id> | python3 -m json.tool
```

`approvals` shows the reviewer's Microsoft account name, the decision and the comment, and the audit trail ends with `notify_reviewer` and `human_review`.

## Controls

- The flow URL, the approval token and the tunnel address are configuration, never committed (`.env`).
- `/approve` rejects requests without the token (401), decides each run once (409), and only for runs that need review (409).
- If the flow is unreachable, the investigation still completes and the failed notification is recorded in the audit log.
- The tunnel is for the demo. In production the API would sit behind the enterprise gateway and the flow would use a service account connection.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/health` shows `reviewer_workflow: none` | Flow URL not loaded | Quote every value in `.env` (the flow URL contains `&`) |
| Flow HTTP step fails with `UnresolvableHostName` | `PUBLIC_BASE_URL` points at an old or placeholder tunnel | `unset PUBLIC_BASE_URL`, check `.env`, restart the API, start a new run |
| Flow run stays "Running" after approval | HTTP step retrying a failed callback | Open the run, read the HTTP step error |
| `401` on `/approve` | Token header does not match `APPROVAL_TOKEN` | Paste only the token value into the flow header |
