# Technical notes

How the app is built, for developers. Setup is in the [README](../README.md), deployment in [DEPLOY.md](../DEPLOY.md).

## Stack

| Layer | Choice | Cost |
|---|---|---|
| Frontend | Next.js 15, React 19, TypeScript, Tailwind | Open source |
| Backend | FastAPI, Python 3.12 | Open source |
| Speech-to-text | AssemblyAI Universal-Streaming (default) or Deepgram Nova-3 | Free signup credit |
| AI | Groq: gpt-oss-120b for answers, gpt-oss-20b for question detection (default), or any OpenAI-compatible API | Free tier |
| Embeddings | fastembed, BAAI/bge-small-en-v1.5, runs on the server | Open source |
| Vector search | Pinecone | Free plan |
| CV/JD parsing | pypdf (BSD), python-docx (MIT) | Open source |
| Database, cache | PostgreSQL, Redis | Open source |
| Sign-in | Auth.js (NextAuth) with the backend's JWT; Google optional | Open source |
| Payments | Stripe Checkout, Customer Portal, webhooks | Per-transaction fee only |

PyMuPDF is deliberately not used: it is AGPL-3.0, which conflicts with a closed-source SaaS.

## Live session

One WebSocket per interview (`backend/app/api/session_ws.py`, which documents the message protocol).
The browser sends 16 kHz PCM audio from two channels (interviewer tab, candidate microphone); the
server streams each channel to the speech provider and sends back transcript, question, answer and
usage messages. Question detection runs on each finished interviewer turn and on long unfinished
turns; CV search runs in parallel with detection. Session logic: `backend/app/copilot/session.py`.

Answer safety (`backend/app/copilot/prompts.py`, `factcheck.py`): answers may use only CV facts;
every number in an answer must appear in the CV or it is shown as "Check before saying"; questions
about failures, weaknesses or salary get a fill-in framework instead of an invented story.

## Swapping providers

Each provider sits behind an interface (`backend/app/providers/base.py`) and registers under a name;
`.env` picks which one runs. No code changes are needed:

| Setting | Options |
|---|---|
| `STT_PROVIDER` | `assemblyai` (default), `deepgram` |
| `LLM_PROVIDER` | `groq` (default), `openai_compatible` (OpenRouter, Ollama, OpenAI, Groq...; see `.env.example`) |
| `EMBEDDING_PROVIDER` | `local` (fastembed) |
| `VECTOR_PROVIDER` | `pinecone` |

A new provider is one file that implements the interface and registers a name (see `deepgram_stt.py`
or `openai_compat_llm.py`), plus a line in `load_providers()`. `tests/test_providers_swap.py` runs a
full interview on Deepgram to show the rest of the app is unaffected.

## Reliability and scaling

- **Several backend instances.** State shared between instances lives in Redis (`REDIS_URL`, required
  outside development): sign-in and sign-up limits, and live-session tickets. Everything else is in
  Postgres. Any instance can serve any request.
- **Live-session sign-in.** The browser exchanges its access token for a single-use ticket
  (`POST /api/session/ticket`, valid 30 s) and connects with `?ticket=`, so long-lived tokens never
  appear in URLs or logs.
- **Reconnect.** If the connection drops mid-interview (network, server restart, redeploy), the
  browser reconnects by itself (5 attempts over about 30 s) and the server continues the same
  interview record; questions already answered are not answered again. A dropped speech stream is
  reopened by the server (3 attempts). Both speech streams connect in parallel at the start.
- **Latency.** Every answer writes one `latency` JSON line to the log (detection, CV search, first
  words, full answer, in ms, measured from the interviewer's final words). Accounts in `ADMIN_EMAILS`
  see medians and 95th percentiles at `/admin/latency`.
- **Errors.** In development, messages name the cause (for example a missing key in `.env`);
  elsewhere users see plain messages and the details go to the log.
- **Database migrations** run automatically at startup, guarded by a Postgres advisory lock so
  several instances can start at once (`backend/app/db/migrations.py`).

## Security

- Passwords: bcrypt. Tokens: HS256 JWT; `JWT_SECRET` of 32+ characters is required outside
  development. A deleted account's token stops working at once.
- Rate limits (Redis): 8 failed sign-ins per email and network per 15 minutes; 10 new accounts per
  network per hour; Regenerate at most once a second. Keys are hashed, so no emails are stored in Redis.
- Every query is scoped to the signed-in user; CV vectors live in a per-user Pinecone namespace.
  Deleting an account deletes its documents, vectors, history and Stripe subscription.
- Headers: Content-Security-Policy (only this backend may be contacted), no framing, no MIME
  sniffing, HSTS in production, microphone and tab capture allowed for this site only. API
  responses are `no-store`. API docs (`/docs`) are off outside development.
- CI runs `pip-audit` and `npm audit` on every push.

## Payments (Stripe)

- Plans, prices and minute limits: `backend/app/billing/plans.py`. The prices are examples.
- The plan changes only when a signed Stripe webhook says so, never from the browser. Late, repeated
  or out-of-order events are handled: each event re-reads the subscription's current state from
  Stripe, and an older event never overwrites a newer one.
- A failed renewal (`past_due`) keeps the plan while Stripe retries the card; `unpaid` or `canceled`
  returns the account to Free.
- Deleting an account deletes the Stripe customer first, which cancels the subscription; if Stripe
  can't be reached, nothing is deleted.

**Going live:** in Stripe, create a product with two monthly prices (Pro, Premium), add a webhook
endpoint `https://<api address>/api/billing/webhook` with the events `checkout.session.completed`
and `customer.subscription.created/updated/deleted`, turn on the Customer Portal, then set
`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO` and `STRIPE_PRICE_PREMIUM`.

**Testing without a Stripe account:** set `STRIPE_WEBHOOK_SECRET=whsec_local_test`,
`STRIPE_PRICE_PRO=price_local_pro` and `STRIPE_PRICE_PREMIUM=price_local_premium` in `.env`, restart
the backend, then send signed test events:

```bash
cd backend
python -m scripts.stripe_event --email you@example.com --plan pro      # subscribe
python -m scripts.stripe_event --email you@example.com --status past_due
python -m scripts.stripe_event --email you@example.com --cancel        # back to Free
```

The local test secret is refused outside development, so it can't be used on a live server.

## Tests

```bash
cd backend
pytest -q
```

Some tests need real services and are skipped without them: set `TEST_DATABASE_URL` (Postgres),
`TEST_REDIS_URL` (Redis) and `STRIPE_MOCK_URL=http://localhost:12111` (with
`docker run -p 12111:12111 stripe/stripe-mock`). CI sets all three.

Answer quality: `python -m eval.run_eval` (question detection) and `python -m eval.run_eval --answers`
(answer checks against a test CV; uses the Groq key).
