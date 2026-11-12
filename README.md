# AI Interview Copilot

A web app that listens to a live video interview and helps the candidate answer. It transcribes the
interviewer, recognises each question, and shows a short answer built only from the candidate's own
CV and the job description, in about two seconds.

It works with any interview that runs in Chrome or Edge (Google Meet, Zoom, Teams in the browser).

## Features

- **Audio capture:** the interview tab's audio and the candidate's microphone, captured in the browser.
- **Live transcript:** both speakers, word by word.
- **CV and job description:** upload PDF, Word or text; the content is split, embedded and searched per question.
- **Streamed answers:** a STAR story (Situation, Task, Action, Result) for behavioural questions, a short outline for
  technical ones. Answers use only facts from the CV, and any number not found in the CV is flagged before it is said.
- **Accounts and plans:** sign-in with email (Google optional), dashboard, interview history, plans with monthly
  minute limits, payments through Stripe, one file for branding.
- **Reliability:** answer-speed logging, automatic reconnect, swappable speech and AI providers, automated tests,
  security hardening.
- **Deployment:** Docker images; runs as several instances behind a load balancer.

## How it works

```
Browser (tab audio + mic) ──> Backend ──> speech-to-text (AssemblyAI or Deepgram)
                                 │
                                 ├── question detection and answers (Groq or any OpenAI-compatible AI)
                                 ├── CV search (local embeddings + Pinecone)
                                 └── Postgres (accounts, documents, history) + Redis (shared short-lived state)
```

API keys stay on the server; the browser only talks to the backend.

## Run it on your computer

Needs Python 3.12+, Node.js 20+ and Docker Desktop.

```bash
# 1. Settings: copy the templates, then fill in the keys (each line says where to get it)
cp .env.example .env
cp frontend/.env.example frontend/.env.local

# 2. Database and Redis
docker compose up -d postgres redis

# 3. Backend
cd backend
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000

# 4. Frontend (in a second terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:3000, create an account, upload a CV on the Dashboard, and start an interview on the Copilot page.

## Keys you need

All have a free tier. The full list, with defaults and options, is in `.env.example`.

| Key | What it is for | Where to get it |
|---|---|---|
| `ASSEMBLYAI_API_KEY` | Speech-to-text | assemblyai.com (free credit) |
| `GROQ_API_KEY` | Question detection and answers | console.groq.com (free tier) |
| `PINECONE_API_KEY` | CV search | pinecone.io (free plan) |
| `JWT_SECRET`, `INTERNAL_API_SECRET` | Sign-in security | any random 32+ character strings |
| `NEXTAUTH_SECRET` (frontend) | Session cookie encryption | any random 32+ character string |
| `STRIPE_*` | Payments (optional until launch) | dashboard.stripe.com |
| `GOOGLE_CLIENT_ID/SECRET` (frontend) | "Continue with Google" (optional) | console.cloud.google.com |

## Project structure

```
backend/                 FastAPI (Python)
  app/api/               HTTP and WebSocket endpoints
  app/copilot/           live session: question detection, answers, history recording, usage meter
  app/providers/         speech, AI, embedding and vector providers (swappable in .env)
  app/retrieval/         CV and job description chunking and search
  app/billing/           plans and Stripe
  app/db/                database connection and migrations (run automatically)
  tests/                 automated tests
  eval/                  answer-quality test set (python -m eval.run_eval)
frontend/                Next.js (TypeScript, Tailwind)
  src/app/               pages
  src/config/brand.ts    branding: name, text, colours
deploy/                  load balancer config and server scripts
docs/TECHNICAL.md        how it is built: providers, reliability, security, payments
DEPLOY.md                putting it live (Vercel or a server), operations, backups
docker-compose.yml       local database and Redis
docker-compose.prod.yml  production stack
render.yaml              Render setup (Render requires a card)
```

## Tests and checks

```bash
cd backend
pytest -q          # automated tests
ruff check .       # code style
mypy app scripts   # type checks
```

The same checks, plus a build and start of the full production stack, run on GitHub for every push
(`.github/workflows/ci.yml`).

## Putting it live

See [DEPLOY.md](DEPLOY.md): free on Vercel (no card), or the full multi-instance setup on any server.

## Customising

- Name, text and colours: `frontend/src/config/brand.ts`
- Plans, prices and minute limits: `backend/app/billing/plans.py` (the prices there are examples)
- Speech or AI provider: `STT_PROVIDER` and `LLM_PROVIDER` in `.env` (see [docs/TECHNICAL.md](docs/TECHNICAL.md))
