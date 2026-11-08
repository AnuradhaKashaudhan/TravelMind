# TravelMind

TravelMind is an autonomous multi-agent AI travel-planning platform. This
repository is being built in ten phases; Phases 1 and 2 are complete, and
Phase 3 (LLM, external tool, and caching integrations) is in progress.

## Architecture

```mermaid
flowchart LR
    User[User] --> Frontend[Next.js frontend<br/>Phase 9]
    Frontend --> API[FastAPI backend]
    API --> Services[Application services]
    API --> PostgreSQL[(PostgreSQL + pgvector)]
    API --> Redis[(Redis)]
    Services --> Workflow[LangGraph orchestration<br/>Phase 4]
    Workflow --> Agents[Travel agents<br/>Phase 5]
    Agents --> Integrations[Tools and LLM integrations<br/>Phase 3]
    Integrations --> Providers[Gemini, OpenAI-compatible,<br/>maps, weather]
```

## Requirements

- Docker Desktop with Docker Compose
- Python 3.11 or newer for local development outside Docker

## Local setup

1. Copy `.env.example` to `.env` and set credentials as needed. The supplied
   values are development-only.
2. Start the API and infrastructure:

   ```powershell
   docker compose up --build -d
   ```

3. Apply the database migration:

   ```powershell
   docker compose exec backend alembic upgrade head
   ```

4. Start the isolated PostgreSQL/pgvector test database and run the backend
   tests against it:

   ```powershell
   docker compose --profile test up -d postgres-test
   $env:TEST_DATABASE_URL = "postgresql+asyncpg://travelmind:travelmind@localhost:5433/travelmind_test"
   docker compose run --build --rm --no-deps -e TEST_DATABASE_URL="postgresql+asyncpg://travelmind:travelmind@postgres-test:5432/travelmind_test" backend pytest
   ```

5. Check the API:

   ```powershell
   Invoke-RestMethod http://localhost:8000/health
   Invoke-RestMethod http://localhost:8000/version
   ```

5. To run migrations through the full reversible cycle:

   ```powershell
   docker compose exec backend alembic downgrade -1
   docker compose exec backend alembic upgrade head
   ```

Use `make up`, `make down`, `make logs`, `make migrate`, `make test`,
`make lint`, and `make format` where GNU Make is available.

## Phase 3 integrations

- LLM calls go through `app.llm.router.LLMRouter`; Gemini and configurable
  OpenAI-compatible endpoints implement the same async interface. Set
  `LLM_PROVIDER`, optional `FALLBACK_LLM_PROVIDER`, provider credentials, and
  optional `SIMPLE_MODEL`, `SUPERVISOR_MODEL`, and `ITINERARY_MODEL` overrides.
- Embeddings are validated at 768 dimensions to match the pgvector columns.
  Structured responses use Pydantic validation and at most two repair attempts.
- External integrations are typed `Tool` implementations. Google Maps tools
  use `GOOGLE_MAPS_API_KEY`; weather uses Open-Meteo without a key and can use
  OpenWeatherMap when `WEATHER_API_KEY` is configured. Fares are labeled
  estimates and configured in `backend/app/tools/fare_estimates.json`.
- Redis caches use provider-specific TTL settings. Transient errors are retried
  with bounded backoff; API keys are never included in log fields.
- Set `USE_FAKE_PROVIDERS=true` to select no-network LLM and tool fakes for
  local development. Fake travel outputs are placeholders, not live data.

Run unit tests, lint, and type checking from `backend`:

```powershell
python -m pytest
ruff check app tests scripts
mypy app
python scripts/smoke_tools.py
```

The smoke script uses configured live providers by default. To run it against
real services, set `GEMINI_API_KEY`, `GOOGLE_MAPS_API_KEY`, and optionally
`WEATHER_API_KEY` in `.env`, then run `python scripts/smoke_tools.py`. For
no-network behavior, set `USE_FAKE_PROVIDERS=true`.

## Phase checklist

- [x] Phase 1 — Project Foundation
- [x] Phase 2 — Data models and schemas
- [ ] Phase 3 — LLM abstraction, external tools, caching (in progress)
- [ ] Phase 4 — Agent orchestration
- [ ] Phase 5 — Agents
- [ ] Phase 6 — Planning workflows
- [ ] Phase 7 — API capabilities
- [ ] Phase 8 — Reliability and observability
- [ ] Phase 9 — Frontend
- [ ] Phase 10 — Deployment and production readiness
