# TravelMind

TravelMind is an autonomous multi-agent AI travel-planning platform. This
repository is being built in ten phases; Phase 1 established the backend
foundation and Phase 2 defines its data layer.

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
    Agents --> Integrations[Tools and LLM integrations<br/>Phases 3 and 5]
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

## Phase checklist

- [x] Phase 1 — Project Foundation
- [ ] Phase 2 — Data models and schemas (in progress)
- [ ] Phase 3 — Integrations and tools
- [ ] Phase 4 — Agent orchestration
- [ ] Phase 5 — Agents
- [ ] Phase 6 — Planning workflows
- [ ] Phase 7 — API capabilities
- [ ] Phase 8 — Reliability and observability
- [ ] Phase 9 — Frontend
- [ ] Phase 10 — Deployment and production readiness
