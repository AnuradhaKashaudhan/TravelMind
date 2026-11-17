# TravelMind project instructions

## Architecture

TravelMind is an async-first travel-planning platform. The backend uses FastAPI,
Pydantic v2, SQLAlchemy 2.0 async, PostgreSQL with pgvector, and Redis. LangGraph
orchestration is implemented in Phase 4; specialist agents are added in Phase 5.
Gemini and OpenAI-compatible model integrations are planned for Phase 3. The
Next.js/React/TypeScript/Tailwind frontend is scaffolded in Phase 9. Docker Compose
provides local infrastructure.

## Folder responsibilities

- `backend/app/api/`: FastAPI routers and API boundary code.
- `backend/app/agents/`: focused agent implementations (Phase 5).
- `backend/app/orchestration/`: LangGraph workflows (Phase 4).
- `backend/app/tools/` and `backend/app/llm/`: integrations and model clients
  (Phase 3).
- `backend/app/models/` and `backend/app/schemas/`: persistence models and
  validated data contracts.
- `backend/app/db/repositories/`: async persistence access; keep transaction
  boundaries with the caller and avoid domain/business workflows here.
- `backend/app/services/`: application use cases and domain coordination.
- `backend/app/core/`: configuration, structured logging, exceptions, and
  security primitives.
- `backend/app/db/`: database base, engine, and session lifecycle.
- `backend/tests/`: automated backend tests.
- `backend/alembic/`: versioned database migrations.
- `frontend/`: future Next.js frontend.

## Coding standards

- Add type hints to every function and public module-level value.
- Prefer async I/O and async APIs for network and database work.
- Use Pydantic models for all agent inputs and outputs.
- Never hardcode credentials, tokens, or production secrets.
- Keep functions small and single-purpose; document modules and functions with
  clear docstrings.
- Use structured logging; include request/trace context where available.
- Bound retries with explicit limits and backoff; never retry indefinitely.
- Add tests for every module and meaningful behavior change.
- Keep dependencies, configuration, and migrations explicit and reproducible.
- Use UUID primary keys and timezone-aware shared timestamps for persisted
  entities; model relationships with explicit foreign-key `ondelete` behavior.
- Store structured PostgreSQL data as JSONB and vector embeddings with pgvector;
  include indexes and reversible Alembic migrations for schema changes.
- Keep Pydantic API schemas distinct from SQLAlchemy models, and define agent
  inputs/outputs as validated Pydantic contracts.
- Booking and payment actions must always require explicit user approval before
  execution. No agent or automation may bypass this requirement.

## Phase 3 LLM and tool conventions

- Route model calls through `app.llm.router.LLMRouter`; provider integrations
  implement `app.llm.base.LLMClient` and must not leak SDK-specific response
  objects into application code.
- Parse generated structured output into the requested Pydantic model. The
  shared structured-output path allows no more than two repair attempts.
  Embeddings must remain exactly 768 dimensions to match `Vector(768)`.
- Add external integrations as typed `Tool` subclasses with explicit Pydantic
  input and output models. Parse every provider response before returning it;
  never return provider dictionaries or log credentials/request secrets.
- Tool retries are bounded and apply only to timeouts, HTTP 429, and HTTP 5xx.
  Keep per-provider rate limiting, circuit breakers, cache TTLs, and tool
  timeouts configurable. Cache only normalized Pydantic results.
- Mark heuristic or simulated transport prices as estimates and include their
  source. Booking and payment remain outside this phase and always require
  explicit user approval.
- Keep Phase 3 integrations independent of specialist agent implementations.

## Phase 4 orchestration conventions

- Keep graph state and task/result contracts typed and checkpoint-serializable.
  Parallel state updates must use explicit merge reducers.
- Validate supervisor task keys, dependencies, and cycles before execution.
  Agent implementations are registered by stable name; plans may reference
  only registered agents.
- Execute independent ready tasks concurrently with a bounded semaphore.
  Retry only transient failures, with configured timeouts and bounded jittered
  backoff; a failed task must not restart the entire run.
- Persist each task transition and result through repositories using a
  service-owned transaction. Never share an `AsyncSession` between concurrent
  tasks.
- Use the graph checkpointer for interrupt/resume and reconcile persisted
  successful tasks before retrying work after a crash.
- Booking/payment tasks must enter the approval gate and remain unexecuted until
  an explicit approval is recorded. Keep event publication transport-neutral;
  WebSocket delivery belongs to Phase 7.
- Tests and demos use `FakeLLMClient` and registered stub agents. Do not add
  real specialist agents, API routes, WebSockets, or memory/replanning logic
  before their designated phases.

## Phase 5 specialist research conventions

- Destination, transport, accommodation, weather, activity, and local
  intelligence agents use typed Pydantic output and `ResearchAgent`.
- Never present an LLM-proposed place as real until Places grounding resolves
  and verifies it. Provider unavailability must produce `verified=false` with a
  clear note, not a hallucinated place.
- Transport and accommodation prices are estimates and must carry
  `is_estimate=true` plus a source; they are not live fares.
- Tool or LLM failures degrade to a valid `AgentResult` with errors and
  confidence/source metadata so the executor can persist and recover them.
