# Outpost

A daily narrative roguelike. Four AI-driven colonists run a fragile colony under your command. You lead; they argue, remember, form alliances, and can move against you. Each run produces a unique story, ending in a post-mortem timeline the player can share.

**Status:** Specification locked. Implementation in progress.

## Game overview

- 8–12 turn runs, 15–25 minutes each
- Permadeath — agents can die, the colony can fail, the leader can be killed
- Three interlocking mechanics: Grievance Stack, Delayed Consequences, Legitimacy Meter
- Three launch settings: arctic research station, orbital colony, desert outpost
- One run per day per account

Inspirations: Papers Please, Don't Starve, Detroit: Become Human, Rimworld, Frostpunk.

## Architecture at a glance

- **Backend:** FastAPI + LangGraph (state graph) + raw Anthropic SDK (Haiku-dominant, prompt-cached, debate-batched) on Railway
- **Frontend:** Next.js 16 + Tailwind + shadcn/ui (pixel-art themed) + DiceBear portraits on Vercel
- **Data:** Supabase Postgres + Supabase Auth (Google OAuth)
- **Observability:** Langfuse (traces) + LangSmith `agentevals` (CI evals)

Full architecture in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Documentation

- [`docs/SPEC.md`](docs/SPEC.md) — game design, mechanics, tone, MVP criteria
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — technical architecture
- [`docs/PLAN.md`](docs/PLAN.md) — phased implementation plan
- [`docs/research/`](docs/research/) — research source docs for architectural decisions
- [`CLAUDE.md`](CLAUDE.md) — conventions and invariants for AI-assisted development

## Getting started (local dev)

Prerequisites: Docker, Node 22+, Python 3.12+, `uv`, Supabase CLI, Railway CLI, `gh`.

```bash
git clone git@github.com:luclacombe/outpost-ai-roguelike.git
cd outpost-ai-roguelike

# Backend
cd backend
cp .env.example .env  # fill in Supabase, Anthropic, Langfuse keys
supabase start
docker compose up -d

# Frontend (separate terminal)
cd ../frontend
cp .env.local.example .env.local
pnpm install
pnpm dev
```

Full setup instructions in [`docs/PLAN.md`](docs/PLAN.md) Phase 0.

## License

MIT — see [LICENSE](LICENSE).
