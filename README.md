# Outpost

A daily browser roguelike where four AI advisors run a fragile colony, argue about what to do next, hold grudges, and sometimes try to kill you.

You play the leader. Each day you get one run. It lasts 8–12 turns, ends in death or rescue or coup, and produces a post-mortem timeline you can share.

> **Status (October 2026):** playable at [outpost-ai-roguelike.vercel.app](https://outpost-ai-roguelike.vercel.app) with Google sign-in, one run a day. That's the turn-based version this README describes. It works, but my first full run in production showed it isn't much of a game yet: my choices felt disconnected from the numbers, and reading four advisors argue got old fast. So I'm rebuilding it as a short real-time holdout on a new deterministic engine. The current version stays up until the new one is playable end to end. See [Where it's going](#where-its-going).

## Why this exists

I wanted to find out whether four small models, given personalities and grievances and a shared world to mutate through tool calls, could produce stories that feel like a person wrote them. Not "the AI wrote a story." Stories where you can point at turn six and say *that's the moment Marisol stopped trusting me, and that's why she sided with Kel later.*

Most LLM-driven games either let the model freely narrate (fun, but the world doesn't actually change) or pin everything down so tightly the model is just a flavor layer. Outpost picks a middle path: a deterministic engine owns state, the agents propose changes only through validated tool calls, and a single batched debate call decides the turn. The debate is one forced structured tool call that returns every advisor's reaction plus three options, so four advisors cost one LLM call, not four.

Some of it works. Some of it I would do differently next time. It's all in `docs/PLAN.md`'s "Errors & Learnings" log.

## What's in the box

- **Four advisors** with three personality axes (Order/Liberty, Collective/Individual, Cautious/Bold) and persistent memory across turns. They form alliances. They die. Dead means dead.
- **Three interlocking systems:** a Grievance Stack (advisors track every choice you make against them), a Delayed Consequence log (decisions seed events 1–3 turns later), and a Legitimacy meter (drops too far and the colony stops obeying).
- **Three launch settings:** arctic research station, orbital colony, desert outpost. Each is a YAML schema, so adding a fourth setting is a config change, not code.
- **Permadeath end-states:** colony failure, coup, leader assassinated, evacuation, partial survival, full survival. The post-mortem timeline shows the chain of decisions that got you there, plus counterfactuals the engine ran on the branches you didn't take.
- **One run per account per day**, server-authoritative. Resets at UTC midnight.
- **Shareable post-mortem** with an OG image card sized for Twitter, Bluesky, and Discord.

Inspirations: Papers Please, Don't Starve, Detroit: Become Human, Rimworld, Frostpunk.

## Stack

- **Backend:** FastAPI (Python 3.12), LangGraph for the turn graph, and the raw Anthropic SDK pointed at any Anthropic-compatible endpoint. Production runs DeepSeek V4.1 Flash through OpenRouter, prompt-cached, with the debate as one forced tool call. Deployed on Railway.
- **Frontend:** Next.js 16 App Router, TypeScript strict, Tailwind v4, shadcn/ui re-themed for pixel-art, DiceBear for portraits. Deployed on Vercel.
- **Data:** Supabase Postgres via SQLAlchemy async + psycopg3. Supabase Auth (Google OAuth) with JWTs validated locally in FastAPI.
- **Observability:** every LLM call's model, tokens and billed cost are stored with its turn. Langfuse traces the LangGraph steps. LangSmith `agentevals` runs the merge-gate evals (personality consistency, tool-call validity, narrative repetition, decision coherence, game balance).

Why these choices? `docs/research/` has the long version. The short one: the raw SDK keeps cache and tool-call control honest, LangGraph gives durable state across the SSE pause/resume, and Supabase is the cheapest way to get OAuth and Postgres in one place.

The model was picked blind. Twenty models played 12 real turns, the game's own validator threw out broken output, and four Opus judges scored the rest without knowing which model wrote what. The finalists then played full games. DeepSeek V4.1 Flash matched the previous model's quality at 1.41 cents a game in real spend, down from about 6.

## Repo layout

```
Outpost/
├── docs/                    SPEC, ARCHITECTURE, PLAN, research/
├── backend/                 FastAPI app
│   ├── app/                 game engine, agent graph, auth, eval
│   ├── tests/               unit + integration + eval (markered)
│   ├── alembic/             ORM migrations (public schema)
│   └── supabase/migrations/ CLI migrations (auth schema, RLS)
├── frontend/                Next.js app
│   ├── app/                 (public) + (authed) route groups
│   ├── components/          dashboard panels, post-mortem timeline
│   └── hooks/               SSE consumption
├── .github/workflows/       test, eval, deploy
├── CLAUDE.md                conventions for AI-assisted development
└── README.md                you are here
```

## Local development

Prerequisites: Docker, Node 22+, Python 3.12+, [`uv`](https://docs.astral.sh/uv/), [Supabase CLI](https://supabase.com/docs/guides/cli), [Railway CLI](https://docs.railway.com/guides/cli) (for deploy), `gh`.

```bash
git clone git@github.com:luclacombe/outpost-ai-roguelike.git
cd outpost-ai-roguelike

# Backend (terminal 1)
cd backend
cp .env.example .env                  # fill in Supabase + LLM provider keys
supabase start                        # local Postgres on 54322
docker compose up -d                  # FastAPI on 8000 with reload

# Run unit tests (mocked LLM, no network)
uv run pytest tests/ -v -m "not eval and not integration"

# Frontend (terminal 2)
cd frontend
cp .env.local.example .env.local
pnpm install
pnpm dev                              # http://localhost:3000
```

Phase 0 in `docs/PLAN.md` has the full setup walkthrough including OAuth provider config and Supabase RLS bootstrap.

## Deploy

Frontend deploys via Vercel's GitHub integration. Backend deploys via Railway, either through Railway's GitHub integration (recommended) or via the included `.github/workflows/deploy.yml` (set `RAILWAY_TOKEN` as a repo secret to enable).

### First-time setup

1. **Vercel:** import the repo, root directory `frontend/`. Set env vars: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_BASE_URL`. Vercel auto-deploys on push to `main`.
2. **Railway:** create a project, connect this GitHub repo, set service root to `backend/`. Railway auto-detects the Dockerfile. Copy every var from `backend/.env.example` into the Railway service env, plus `FRONTEND_URL` set to your Vercel domain (this is the CORS allowlist).
3. **Supabase auth:** in the Supabase dashboard → Authentication → URL Configuration, add the Vercel domain to "Site URL" and add `https://<vercel-domain>/auth/callback` to "Redirect URLs."

The full env catalog with sources for each value lives in `docs/ARCHITECTURE.md` §14.

### Cold-start note

On Railway's free tier the backend can idle-sleep, adding 1–3s to the first request after a quiet period. For a daily-run game that's fine. If it starts mattering, upgrade to Pro with `min_machines_running = 1`, or run a 5-minute keep-alive cron against `/health`.

## Tests and CI

```bash
# Backend
cd backend
uv run pytest tests/ -v                             # default: unit only (mocked)
uv run pytest -m integration                        # real LLM, costs tokens
uv run pytest -m eval                               # full eval pipeline, costs tokens
uv run ruff check app/ && uv run ruff format --check app/

# Frontend
cd frontend
pnpm lint
pnpm build
```

Three workflows in `.github/workflows/`:

- `test.yml`: backend ruff + unit tests + frontend lint + build, on every PR and push to main.
- `eval.yml`: real-LLM eval pipeline, on PRs that touch `backend/**` or `docs/PLAN.md`. Has a hard $0.50/run budget ceiling enforced as a pytest assertion.
- `deploy.yml`: backend Railway deploy on push to main, gated by inline test job.

The eval gate blocks merges that drop personality consistency below 0.72 or tool-call validity below 85%. See `docs/ARCHITECTURE.md` §12.

## Where it's going

The live game proved the plumbing: auth, the daily gate, streamed turns, cost tracking, evals. It didn't prove the fun. The rebuild goes in this order:

1. A deterministic TypeScript engine plus a headless simulator with bots, so balance gets tuned against thousands of runs instead of my gut.
2. An ugly but playable version. If it isn't fun as grey boxes, no LLM work starts.
3. Server-side replay of each run's action log, so a score can't be faked from the browser.
4. The LLM layers: each day's content generated overnight and checked in the simulator before anyone plays it, and advisors who act on their own.
5. A post-mortem built from cause records, where every number on screen links back to whatever changed it.

Two lines sum up the goal. For players: the colony you play today was evolved last night. For the engineering: every number has a receipt. The design docs live in [`docs/superpowers/specs/`](docs/superpowers/specs/).

## Documentation

- [`docs/SPEC.md`](docs/SPEC.md): game design, mechanics, tone, MVP criteria
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): technical architecture, env vars, deploy
- [`docs/PLAN.md`](docs/PLAN.md): phased implementation plan, errors & learnings log
- [`docs/research/`](docs/research/): research source material for architectural decisions
- [`docs/superpowers/`](docs/superpowers/): newer specs, plans and handoffs (October 2026 onward)
- [`CLAUDE.md`](CLAUDE.md): conventions and invariants for AI-assisted development on this repo

## License

MIT. See [LICENSE](LICENSE).
