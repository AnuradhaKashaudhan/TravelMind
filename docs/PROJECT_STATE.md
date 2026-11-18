# TravelMind Project State Audit

**Audit date:** 2026-11-17
**Scope:** Current repository state through Phase 6 implementation. This document
retains the original Phase 1–3 audit detail below and adds the current workflow
status.

## Current phase status

- Phases 1–5 are complete and committed.
- Phase 6 is in progress: deterministic Decimal budget optimization,
  itinerary generation, validation, bounded repair, final TripVersion
  persistence, evaluation metrics, and the full-plan fake demo are implemented.
- Phase 7 API routes/WebSockets and Phase 8 memory/replanning remain out of scope.

## 1. Project tree

The tree below covers `backend/`, excluding `__pycache__`, caches, and `*.egg-info`.

```text
backend/
├── Dockerfile — Builds and runs the Python backend container.
├── alembic.ini — Alembic configuration and migration script location.
├── pyproject.toml — Package metadata, declared dependencies, and tool configuration.
├── alembic/
│   ├── env.py — Async SQLAlchemy Alembic environment.
│   ├── script.py.mako — Template for generated revisions.
│   └── versions/
│       ├── 0001_enable_vector_extension.py — Enables PostgreSQL's vector extension.
│       └── 4e0af42457f6_create_domain_data_schema.py — Creates domain tables and indexes.
├── app/
│   ├── __init__.py — Backend application package.
│   ├── main.py — FastAPI factory, lifespan, request context, health, and version endpoints.
│   ├── agents/
│   │   └── __init__.py — Empty package reserved for later agent implementations.
│   ├── api/
│   │   └── __init__.py — Empty package reserved for API routers.
│   ├── core/
│   │   ├── __init__.py — Core application package.
│   │   ├── cache.py — Redis JSON cache and cache-aside decorator.
│   │   ├── config.py — Pydantic Settings and process-wide settings loader.
│   │   ├── exceptions.py — Application exception hierarchy and HTTP handlers.
│   │   ├── logging.py — Structured JSON logging and request context helpers.
│   │   └── resilience.py — Async retry, rate-limiter, and circuit-breaker primitives.
│   ├── db/
│   │   ├── __init__.py — Database package.
│   │   ├── base.py — SQLAlchemy declarative base.
│   │   ├── session.py — Async engine, session factory, and session dependency.
│   │   └── repositories/
│   │       ├── __init__.py — Repository package.
│   │       ├── agent_runs.py — Generic CRUD repository for agent-run records.
│   │       ├── base.py — Shared async CRUD repository implementation.
│   │       ├── memory.py — Trip-memory CRUD and cosine-similarity search.
│   │       ├── trips.py — Generic CRUD repository for trips.
│   │       └── versions.py — Trip-version creation with parent-trip locking.
│   ├── llm/
│   │   ├── __init__.py — LLM integration package.
│   │   ├── base.py — Provider-independent client and normalized response contracts.
│   │   ├── factory.py — Configured LLM provider and router construction.
│   │   ├── fake.py — Scriptable no-network LLM provider.
│   │   ├── router.py — Provider selection, fallback, model roles, and usage tracking.
│   │   ├── usage.py — Context-local token and estimated-cost aggregation.
│   │   ├── prompts/
│   │   │   ├── __init__.py — Prompt template package.
│   │   │   ├── loader.py — Loads and renders named, versioned prompt files.
│   │   │   └── trip_summary.v1.txt — Version 1 trip-summary prompt.
│   │   └── providers/
│   │       ├── __init__.py — Provider package.
│   │       ├── gemini.py — Gemini REST generation, streaming, and embeddings.
│   │       └── openai_compatible.py — OpenAI-compatible REST generation and embeddings.
│   ├── models/
│   │   ├── __init__.py — Imports mapped models and exports public model/enumeration names.
│   │   ├── base.py — UUID and timezone-aware timestamp model mixins.
│   │   ├── enums.py — Shared domain enumerations.
│   │   ├── execution.py — Agent-run and task-node persistence models.
│   │   ├── memory.py — Trip memory and pgvector embedding model.
│   │   ├── safety.py — Booking/payment approval-request model.
│   │   ├── sharing.py — Trip collaborators and shared-link models.
│   │   ├── trip.py — Trip, version, itinerary, and budget models.
│   │   ├── types.py — SQLAlchemy enum-column helper.
│   │   └── user.py — User and user-preference models.
│   ├── orchestration/
│   │   └── __init__.py — Empty package reserved for Phase 4 workflows.
│   ├── schemas/
│   │   ├── __init__.py — Exports public API and agent I/O schemas.
│   │   ├── agent_io.py — Typed travel-planning agent input/output contracts.
│   │   ├── approvals.py — Approval request and explicit-decision API schemas.
│   │   ├── common.py — Shared Pydantic configuration and currency helpers.
│   │   ├── trips.py — Trip and trip-version API schemas.
│   │   └── users.py — User and preference API schemas.
│   ├── services/
│   │   └── __init__.py — Empty application-services package.
│   └── tools/
│       ├── __init__.py — External travel-tool package.
│       ├── base.py — Typed tool execution, validation, caching, and resilience.
│       ├── fake.py — No-network fake tool registry and responders.
│       ├── fare_estimates.json — Heuristic fare rates and estimate provenance.
│       ├── geocoding.py — Forward and reverse Google Geocoding tools.
│       ├── google_maps.py — Shared authenticated Google Maps HTTP client.
│       ├── places.py — Places text/nearby search, details, and verification.
│       ├── registry.py — Tool registration and JSON/LangChain exports.
│       ├── routes.py — Google Routes and distance-matrix tools.
│       ├── schemas.py — Validated tool input and normalized output models.
│       ├── transport_estimator.py — Configurable heuristic transport estimates.
│       └── weather.py — Open-Meteo forecast with optional OpenWeather fallback.
├── scripts/
│   └── smoke_tools.py — Manual smoke runner for tool integrations.
└── tests/
    ├── __init__.py — Test package.
    ├── conftest.py — PostgreSQL/pgvector test database fixture.
    ├── test_config.py — Settings coercion and embedding-dimension validation.
    ├── test_health.py — Health and version endpoint behavior.
    ├── test_models.py — Model persistence, constraints, repositories, and schemas.
    ├── test_phase3_providers.py — Mocked Gemini and OpenAI-compatible provider tests.
    ├── test_phase3_registry.py — Fake registry exports and LangChain invocation.
    ├── test_phase3_resilience.py — Retry, breaker, limiter, routing, usage, and structured output.
    ├── test_phase3_tools.py — Tool parsing, cache locking, provider normalization, and estimates.
    └── fixtures/
        ├── geocode.json — Recorded geocoding response.
        ├── open_meteo.json — Recorded Open-Meteo response.
        ├── open_weather.json — Recorded OpenWeather response.
        ├── places_search.json — Recorded Places response.
        ├── route.json — Recorded route response.
        └── route_matrix.json — Recorded route-matrix response.
```

## 2. Phase status

### Phase 1 — Foundation, config, logging, exceptions, health/version, Compose

- **Implemented:** FastAPI app factory and startup/shutdown lifecycle; Pydantic Settings loading environment and `.env`; structlog JSON logging with request/trace context; common application exceptions and HTTP handler; `/health` dependency check and `/version`; Compose services for backend, PostgreSQL/pgvector, Redis, and an optional test-profile PostgreSQL service.
- **Partial:** The API surface is only health/version—there are no trip, user, authentication, or tool endpoints. `jwt_secret` is configured, but no authentication/security implementation is present in the inspected app. Compose has explicit environment mappings but no `env_file`, and does not pass `CORS_ORIGINS`.
- **Missing versus the stated P1 scope:** No other P1 item in the supplied list is absent. The above API/security points are scope limitations, not missing health/config/logging primitives.

### Phase 2 — Models, schemas/contracts, migrations, repositories

- **Implemented:** SQLAlchemy 2 async models for users/preferences, trips and versions, itinerary/budget data, execution/task records, memories, approvals, and sharing; Pydantic API schemas and agent I/O contracts; two linked Alembic revisions; generic async CRUD; trip-memory vector search; transactional next-version allocation. Migrations match current model metadata (`alembic check` passed).
- **Partial:** Repository coverage is narrow: only base CRUD, agent runs, trips, memories, and trip versions have repository classes. User/preferences, approvals, sharing, task nodes, itinerary, and budget entities have no specialized repositories. Several agent output fields intentionally accept `dict[str, Any]`.
- **Missing versus the stated P2 scope:** No missing database models or agent I/O contracts were found for the requested scope. The narrow repository set is the main partial item.

### Phase 3 — LLM, tools, registry, cache, resilience

- **Implemented:** Provider-independent async LLM interface; Gemini and OpenAI-compatible REST clients; fake provider; routing with configured primary/fallback; structured Pydantic output and repair limit; fixed 768-dimension embeddings; role-based model overrides; token/cost tracking; packaged prompt loader; typed Places, geocoding, routes, weather, and fare-estimation tools; registry with JSON function specs and LangChain wrappers; Redis-backed cache; configurable per-provider rate limiting, breakers, timeout, and bounded retry.
- **Partial:** Components are libraries/factories but are not wired into application services, API routes, startup lifecycle, agents, or orchestration. Model selection is by three roles (`simple`, `supervisor`, `itinerary`), not arbitrary per-agent-name mapping. The optional weather fallback requires its configured key.
- **Missing versus the stated P3 scope:** No requested integration category was absent. The planned integration layer exists, but application-level wiring and workflows are not present.

## 3. Database

All persisted models inherit `id UUID`, `created_at timestamptz NOT NULL`, and `updated_at timestamptz NOT NULL`, except that entity-specific columns are listed below. `?` means nullable. PostgreSQL enums store the listed stable values. Named unique constraints are included with the table; their backing unique indexes are database-generated.

### Tables and columns

| Table | Columns (type; `?` = nullable) |
|---|---|
| `users` | `email varchar(320)`, `hashed_password varchar(255)`, `full_name varchar(200)`, `is_active boolean`, `role user_role` |
| `trips` | `user_id UUID`, `title varchar(200)`, `status trip_status`, `origin varchar(255)?`, `destination varchar(255)`, `start_date date?`, `end_date date?`, `traveler_count integer`, `traveler_type traveler_type?`, `total_budget numeric(12,2)?`, `currency varchar(3)`, `requirements JSONB` |
| `user_preferences` | `user_id UUID`, `travel_style varchar(100)?`, `food_preferences JSONB`, `accommodation_preferences JSONB`, `accessibility_needs JSONB`, `preferred_activities JSONB`, `embedding vector(768)?` |
| `agent_runs` | `trip_id UUID`, `supervisor_run_id UUID?`, `agent_name varchar(120)`, `status agent_run_status`, `input JSONB`, `output JSONB?`, `error text?`, `retry_count integer`, `started_at timestamptz?`, `finished_at timestamptz?`, `duration_ms integer?`, `token_usage JSONB?` |
| `approval_requests` | `trip_id UUID`, `action_type approval_action_type`, `payload JSONB`, `status approval_status`, `decided_at timestamptz?` |
| `shared_trip_links` | `trip_id UUID`, `token varchar(128)`, `expires_at timestamptz`, `permission shared_trip_permission` |
| `task_nodes` | `trip_id UUID`, `task_key varchar(160)`, `agent_name varchar(120)`, `depends_on varchar(160)[]`, `priority integer`, `status agent_run_status`, `checkpoint JSONB?` |
| `trip_collaborators` | `trip_id UUID`, `user_id UUID`, `role collaborator_role` |
| `trip_memories` | `user_id UUID`, `trip_id UUID?`, `memory_type memory_type`, `content text`, `metadata JSONB`, `embedding vector(768)?` |
| `trip_versions` | `trip_id UUID`, `version_number integer`, `itinerary JSONB`, `budget_snapshot JSONB`, `change_reason text?`, `created_by varchar(100)` |
| `budget_categories` | `trip_version_id UUID`, `category budget_category_type`, `allocated_amount numeric(12,2)`, `notes text?` |
| `budget_entries` | `trip_version_id UUID`, `category budget_category_type`, `estimated_amount numeric(12,2)`, `notes text?` |
| `itinerary_days` | `trip_version_id UUID`, `day_number integer`, `date date`, `summary text?` |
| `itinerary_items` | `day_id UUID`, `order_index integer`, `item_type itinerary_item_type`, `title varchar(255)`, `description text?`, `start_time time?`, `end_time time?`, `place_id varchar(255)?`, `latitude float?`, `longitude float?`, `estimated_cost numeric(12,2)?`, `currency varchar(3)?`, `metadata JSONB` |

### Enums

- `user_role`: `user`, `admin`
- `trip_status`: `draft`, `planning`, `planned`, `replanning`, `failed`, `archived`
- `traveler_type`: `solo`, `couple`, `family`, `student`, `group`, `business`
- `agent_run_status`: `pending`, `running`, `succeeded`, `failed`, `retrying`, `skipped`
- `approval_action_type`: `booking`, `payment`
- `approval_status`: `pending`, `approved`, `rejected`, `expired`
- `shared_trip_permission`: `viewer`, `editor`
- `collaborator_role`: `owner`, `editor`, `viewer`
- `memory_type`: `preference`, `trip`, `execution`
- `budget_category_type`: `transportation`, `accommodation`, `food`, `activities`, `miscellaneous`
- `itinerary_item_type`: `activity`, `meal`, `transport`, `accommodation`, `free_time`

### Foreign keys, indexes, and constraints

- Foreign keys: `trips.user_id → users.id (CASCADE)`; `user_preferences.user_id → users.id (CASCADE)`; `agent_runs.trip_id → trips.id (CASCADE)` and `supervisor_run_id → agent_runs.id (SET NULL)`; `approval_requests.trip_id → trips.id (CASCADE)`; `shared_trip_links.trip_id → trips.id (CASCADE)`; `task_nodes.trip_id → trips.id (CASCADE)`; `trip_collaborators.trip_id → trips.id (CASCADE)` and `user_id → users.id (CASCADE)`; `trip_memories.user_id → users.id (CASCADE)` and `trip_id → trips.id (SET NULL)`; `trip_versions.trip_id → trips.id (CASCADE)`; `budget_categories.trip_version_id → trip_versions.id (CASCADE)`; `budget_entries.trip_version_id → trip_versions.id (CASCADE)`; `itinerary_days.trip_version_id → trip_versions.id (CASCADE)`; `itinerary_items.day_id → itinerary_days.id (CASCADE)`.
- Explicit indexes: `ix_trips_user_id`, `ix_agent_runs_trip_id`, `ix_agent_runs_supervisor_run_id`, `ix_approval_requests_trip_id`, `ix_shared_trip_links_trip_id`, `ix_trip_memories_trip_id`, `ix_trip_memories_user_id`, `ix_budget_entries_trip_version_id`.
- Vector indexes: `ix_user_preferences_embedding_hnsw` and `ix_trip_memories_embedding_hnsw`, both HNSW with `vector_cosine_ops`. **Confirmed vector dimension: 768** for both columns. The first migration enables the `vector` extension.
- Unique constraints: `users.email`; `user_preferences.user_id`; `shared_trip_links.token`; `(task_nodes.trip_id, task_key)`; `(trip_collaborators.trip_id, user_id)`; `(trip_versions.trip_id, version_number)`; `(budget_categories.trip_version_id, category)`; `(itinerary_days.trip_version_id, day_number)`; `(itinerary_items.day_id, order_index)`.
- Check constraints cover trip date order, positive budget/traveler count, nonnegative run retry/duration, task priority, version number, budget amounts, itinerary day/order, and itinerary item cost/coordinate ranges.

### Alembic revisions (oldest to newest)

1. `0001_enable_vector` — enables the PostgreSQL vector extension; downgrade intentionally leaves the shared extension installed.
2. `4e0af42457f6` — creates the domain schema above; current migration/model drift check found no new operations.

## 4. Pydantic agent contracts

Types below are source annotations; defaults and validation constraints are noted where material. Supporting nested models are expanded so the contract is unambiguous.

- **`TripRequirements`**: `destination: str | None`, `origin: str | None`, `start_date: date | None`, `end_date: date | None`, `traveler_count: int | None`, `traveler_type: TravelerType | None`, `budget: Decimal | None`, `currency: str | None`, `interests: list[str]`, `food: list[str]`, `accommodation: list[str]`, `accessibility: list[str]`, `travel_style: str | None`, `missing_fields: list[str]`. It enforces ordered dates, traveler count ≥ 1, budget > 0, and normalizes currency.
- **`TaskSpec`**: `task_key: str`, `agent_name: str`, `inputs: dict[str, Any]`, `depends_on: list[str]`, `priority: int`. Keys/names have length bounds; priority is nonnegative.
- **`SupervisorPlan`**: `tasks: list[TaskSpec]`, `rationale: str | None`.
- **`DestinationResearchOutput`**: `destination: str`, `overview: str`, `highlights: list[dict[str, Any]]`, `sources: list[str]`.
- **`TransportOutput`**: `options: list[TransportOption]`; `TransportOption` fields: `mode: str`, `description: str`, `estimated_cost: Decimal | None`, `currency: str | None`, `duration_minutes: int | None`.
- **`AccommodationOutput`**: `options: list[AccommodationOption]`; `AccommodationOption` fields: `name: str`, `description: str | None`, `place_id: str | None`, `latitude: float | None`, `longitude: float | None`, `estimated_cost: Decimal | None`, `currency: str | None`.
- **`WeatherOutput`**: `destination: str`, `days: list[WeatherDay]`; `WeatherDay` fields: `date: date`, `summary: str`, `temperature_min: float | None`, `temperature_max: float | None`.
- **`ActivityOutput`**: `activities: list[ActivityOption]`; `ActivityOption` fields: `title: str`, `description: str | None`, `place_id: str | None`, `latitude: float | None`, `longitude: float | None`, `estimated_cost: Decimal | None`, `currency: str | None`, `duration_minutes: int | None`.
- **`LocalIntelligenceOutput`**: `destination: str`, `tips: list[str]`, `safety_notes: list[str]`, `transport_notes: list[str]`, `sources: list[str]`.
- **`BudgetReport`**: `currency: str`, `total_budget: Decimal`, `allocations: list[BudgetAllocation]`, `total_allocated: Decimal`, `total_estimated: Decimal`, `over_budget: bool`, `suggestions: list[str]`. `BudgetAllocation`: `category: BudgetCategoryType`, `allocated_amount: Decimal`, `estimated_amount: Decimal`.
- **`ItineraryPlan`**: `days: list[ItineraryDayPlan]`. `ItineraryDayPlan`: `day_number: int`, `date: date`, `summary: str | None`, `items: list[ItineraryItemPlan]`. `ItineraryItemPlan`: `id: UUID | None`, `item_type: ItineraryItemType`, `title: str`, `description: str | None`, `start_time: time | None`, `end_time: time | None`, `place_id: str | None`, `latitude: float | None`, `longitude: float | None`, `estimated_cost: Decimal | None`, `currency: str | None`.
- **`ValidationReport`**: `issues: list[ValidationIssue]`, `passed: bool`. `ValidationIssue`: `severity: Literal["info", "warning", "error", "critical"]`, `type: Literal["budget", "schedule", "distance", "timing", "consistency"]`, `message: str`, `affected_item_ids: list[UUID]`.
- **`AgentResult`**: `agent_name: str`, `status: AgentRunStatus`, `data: dict[str, Any] | None`, `errors: list[str]`, `sources: list[str]`, `confidence: float | None`, `duration_ms: int | None`.

List fields generally default to new empty lists. Numeric/date constraints are enforced where declared in `agent_io.py`; there is no plan-level validation for duplicate task keys, missing dependency references, or acyclic dependencies.

## 5. Repositories

All write methods flush but do not commit; transaction boundaries remain with the caller.

- `BaseRepository[ModelT]`
  - `__init__(session: AsyncSession, model: type[ModelT]) -> None`
  - `async get(entity_id: UUID) -> ModelT | None`
  - `async list(*, offset: int = 0, limit: int = 100, filters: Mapping[str, object] | None = None) -> list[ModelT]`
  - `async create(**values: object) -> ModelT`
  - `async update(entity: ModelT, values: Mapping[str, object]) -> ModelT`
  - `async delete(entity: ModelT) -> None`
- `TripRepository(BaseRepository[Trip])` and `AgentRunRepository(BaseRepository[AgentRun])`: each defines `__init__(session: AsyncSession) -> None`; otherwise inherits the base API.
- `MemoryRepository(BaseRepository[TripMemory])`: constructor above and `async similarity_search(embedding: Sequence[float], *, limit: int = 10, user_id: UUID | None = None, trip_id: UUID | None = None) -> list[tuple[TripMemory, float]]`.
- `TripVersionRepository(BaseRepository[TripVersion])`: constructor above and `async create_next_version(trip_id: UUID, *, itinerary: dict[str, Any] | None = None, budget_snapshot: dict[str, Any] | None = None, change_reason: str | None = None, created_by: str) -> TripVersion`.

## 6. LLM layer

### Public contracts

- `LLMResponse` fields: `text: str`, `model: str`, `provider: str`, `usage: TokenUsage`, `latency_ms: float`, `finish_reason: str | None`. `TokenUsage`: `prompt_tokens: int = 0`, `completion_tokens: int = 0`, `total_tokens: int = 0`.
- `LLMClient`:
  - `async generate(messages: Sequence[Message], *, temperature: float = 0.2, max_tokens: int = 2048, timeout: float | None = None, model: str | None = None, json_mode: bool = False) -> LLMResponse`
  - `async generate_structured(messages: Sequence[Message], schema: type[ModelT], *, temperature: float = 0.0, max_tokens: int = 2048, timeout: float | None = None, model: str | None = None, max_repairs: int = 2) -> ModelT`
  - `async embed(texts: list[str]) -> list[list[float]]`
  - `stream(messages: Sequence[Message], *, temperature: float = 0.2, max_tokens: int = 2048, timeout: float | None = None, model: str | None = None) -> AsyncIterator[str]`
- `LLMRouter.__init__(clients: dict[str, LLMClient], *, primary_provider: str, fallback_provider: str | None = None, settings: Settings | None = None, rate_limiter: AsyncRateLimiter | None = None, pricing_usd_per_million: dict[str, tuple[float, float]] | None = None) -> None`.
- `LLMRouter.generate(messages: Sequence[Message], *, temperature: float = 0.2, max_tokens: int = 2048, timeout: float | None = None, model: str | None = None, json_mode: bool = False, role: str | None = None) -> LLMResponse`.
- `LLMRouter.generate_for_role(role: str, messages: Sequence[Message], *, temperature: float = 0.2, max_tokens: int = 2048, timeout: float | None = None) -> LLMResponse`.
- `LLMRouter.generate_structured(messages: Sequence[Message], schema: type[ModelT], *, temperature: float = 0.0, max_tokens: int = 2048, timeout: float | None = None, model: str | None = None, max_repairs: int = 2, role: str | None = None) -> ModelT`.
- `LLMRouter.embed(texts: list[str]) -> list[list[float]]`; `stream(messages: Sequence[Message], *, temperature: float = 0.2, max_tokens: int = 2048, timeout: float | None = None, model: str | None = None) -> AsyncIterator[str]`.
- Provider constructors:
  - `GeminiClient(api_key: SecretStr, *, model: str | None = None, embedding_model: str | None = None, dimensions: int = 768, http_client: httpx.AsyncClient | None = None)`.
  - `OpenAICompatibleClient(*, base_url: str | None = None, api_key: SecretStr | None = None, model: str | None = None, embedding_model: str | None = None, dimensions: int = 768, http_client: httpx.AsyncClient | None = None)`.
  - Both implement `generate`, `embed`, `stream` with the `LLMClient` signatures and `async aclose() -> None`.
- `TokenUsageTracker.record(*, prompt_tokens: int, completion_tokens: int, total_tokens: int, cost_usd: float = 0.0) -> None`; `as_dict() -> dict[str, int | float]`. Module helpers: `current_usage_tracker() -> TokenUsageTracker | None`, `bind_usage_tracker(tracker: TokenUsageTracker) -> Token[TokenUsageTracker | None]`, `reset_usage_tracker(token: Token[TokenUsageTracker | None]) -> None`, and `track_usage(tracker: TokenUsageTracker | None = None) -> Iterator[TokenUsageTracker]`.
- `PromptTemplate.load(name: str, version: int = 1) -> PromptTemplate`; instance `render(**values: str) -> str`.
- `FakeLLMClient(responses: Sequence[str | LLMResponse] = (), *, model: str = "fake-model", dimensions: int = 768)`. Its methods are `generate(messages: Sequence[Message], *, temperature: float = 0.2, max_tokens: int = 2048, timeout: float | None = None, model: str | None = None, json_mode: bool = False) -> LLMResponse`, `embed(texts: list[str]) -> list[list[float]]`, and `stream(messages: Sequence[Message], *, temperature: float = 0.2, max_tokens: int = 2048, timeout: float | None = None, model: str | None = None) -> AsyncIterator[str]`.

### Provider and model selection

`build_llm_router(settings: Settings | None = None) -> LLMRouter` reads `LLM_PROVIDER`, optional `FALLBACK_LLM_PROVIDER`, provider model settings, and API-key settings. The router retries configured transient failures, then attempts the distinct configured fallback provider. `SIMPLE_MODEL`, `SUPERVISOR_MODEL`, and `ITINERARY_MODEL` are the only role overrides; supported roles are `simple`, `supervisor`, and `itinerary`. An explicit `model` argument takes precedence. There is no arbitrary agent-name-to-model mapping.

The structured-output default is at most two repairs (`max_repairs=2`). Both providers currently use HTTPX REST calls, returning normalized application types. Streaming is a limitation: router streaming does not accept a role, and does not use the router's retry or circuit-breaker path; it tries fallback only if the primary fails before emitting any chunk.

## 7. Tools

Every `Tool` has `name`, `description`, `input_schema`, `output_schema`, and `provider`, validates payloads with Pydantic, and exposes `async run(payload: InputT | dict[str, object]) -> OutputT`. Provider execution is implemented by each subclass's `_run(payload: InputModel) -> OutputModel`. The base wraps execution with a timeout, transient-only retries, a provider token bucket, and a provider circuit breaker.

Unless noted, tool controls are inherited from Settings: timeout **15 s**, retries **2** (up to 3 attempts), provider rate **5/s with burst 10**, circuit opens after **5 failures** and resets after **30 s**. Rate limiters and breakers are shared process-wide by provider name. Cache TTLs below are Settings defaults; cached values are normalized Pydantic outputs.

| Tool name | Provider key | Input → output | Cache TTL | Exact tool implementation/call |
|---|---|---|---:|---|
| `places_text_search` | `google_places` | `PlaceSearchInput → PlaceSearchOutput` | 86400 s | `_run(payload: PlaceSearchInput) -> PlaceSearchOutput`; facade `PlacesTools.text_search(query: str, max_results: int = 10) -> PlaceSearchOutput` |
| `places_nearby_search` | `google_places` | `PlaceNearbyInput → PlaceSearchOutput` | 86400 s | `_run(payload: PlaceNearbyInput) -> PlaceSearchOutput`; facade `PlacesTools.nearby_search(lat: float, lng: float, radius_m: float = 5000, max_results: int = 10) -> PlaceSearchOutput` |
| `place_details` | `google_places` | `PlaceDetailsInput → PlaceOutput` | 86400 s | `_run(payload: PlaceDetailsInput) -> PlaceOutput`; facade `PlacesTools.get_place_details(place_id: str) -> PlaceOutput` |
| `verify_place` | `google_places` | `PlaceDetailsInput → VerifyPlaceOutput` | 86400 s | `_run(payload: PlaceDetailsInput) -> VerifyPlaceOutput`; facade `PlacesTools.verify_place(place_id: str) -> VerifyPlaceOutput` |
| `geocode` | `google_geocoding` | `GeocodeInput → GeocodeOutput` | 2592000 s | `_run(payload: GeocodeInput) -> GeocodeOutput`; facade `GeocodingTools.geocode(address: str) -> GeocodeOutput` |
| `reverse_geocode` | `google_geocoding` | `ReverseGeocodeInput → GeocodeOutput` | 2592000 s | `_run(payload: ReverseGeocodeInput) -> GeocodeOutput`; facade `GeocodingTools.reverse_geocode(lat: float, lng: float) -> GeocodeOutput` |
| `get_route` | `google_routes` | `RouteInput → RouteOutput` | 21600 s | `_run(payload: RouteInput) -> RouteOutput`; facade `RoutesTools.get_route(origin: str, destination: str, mode: str = "driving") -> RouteOutput` |
| `distance_matrix` | `google_routes` | `DistanceMatrixInput → DistanceMatrixOutput` | 21600 s | `_run(payload: DistanceMatrixInput) -> DistanceMatrixOutput`; facade `RoutesTools.distance_matrix(origins: list[str], destinations: list[str], mode: str = "driving") -> DistanceMatrixOutput` |
| `get_forecast` | `open_meteo` | `ForecastInput → ForecastOutput` | 3600 s | `_run(payload: ForecastInput) -> ForecastOutput`; facade `WeatherTools.get_forecast(lat: float, lng: float, start_date: date, end_date: date) -> ForecastOutput` |
| `estimate_transport` | `transport_estimator` | `FareEstimateInput → FareEstimateOutput` | 0 s (disabled) | `_run(payload: FareEstimateInput) -> FareEstimateOutput`; call via `.run(payload)` |

`ToolRegistry` API: `register(tool: Tool[Any, Any]) -> None`, `get(name: str) -> Tool[Any, Any]`, `names() -> list[str]`, `export_tool_specs() -> list[dict[str, Any]]`, `export_schemas() -> dict[str, dict[str, Any]]`, `as_langchain_tools() -> list[Any]`. `build_default_tool_registry(settings: Settings | None = None) -> ToolRegistry` constructs the ten tools or no-network fakes.

Tools are **LangChain-compatible**: each wraps to a `StructuredTool`, and the registry can return them. It also exports function-tool JSON specifications. LangGraph is a declared dependency and these LangChain tools can be used as graph tools, but there is no LangGraph graph or direct orchestration wiring in the project yet.

## 8. Cache and resilience

### `app/core/cache.py`

- `RedisCache(client: Redis, prefix: str = "travelmind")`; `from_settings() -> RedisCache`; `make_key(namespace: str, key: str) -> str`.
- `async get(namespace: str, key: str, schema: type[T]) -> T | None`; `async set(namespace: str, key: str, value: BaseModel, ttl_s: int) -> None`; `async delete(namespace: str, key: str) -> None`.
- `cached(namespace: str, ttl_s: int, key_builder: Callable[P, str], schema: type[T], cache: RedisCache)` is an async cache-aside decorator. For positive TTLs it uses a namespaced SHA-256 lock key, lock expiry `max(ttl_s, 10)` seconds, and `blocking_timeout=5`; it rechecks after acquiring and runs without the lock if acquisition fails. `Tool.run` uses the same lock timing.
- `close_cache(cache: RedisCache) -> None`; `canonical_json(value: object) -> str`.
- There is no separate public lock abstraction. Lock handling is internal to `cached` and `Tool.run`. Redis errors in cache get/set/delete are logged; reads degrade to a cache miss.

### `app/core/resilience.py`

- `is_transient_error(error: Exception) -> bool`: retries HTTPX timeouts/network errors, status 429, and HTTP 5xx.
- `async retry_async(operation: Callable[[], Awaitable[T]], *, attempts: int = 3, base_delay_s: float = 0.25, max_delay_s: float = 4.0, retry_if: Callable[[Exception], bool] = is_transient_error) -> T`: bounded exponential delay with jitter.
- `AsyncRateLimiter(rate_per_s: float, burst: int)`; `async acquire() -> None`: in-process token bucket.
- `CircuitBreaker(failure_threshold: int = 5, reset_timeout_s: float = 30)`; `async call(operation: Callable[[], Awaitable[T]]) -> T`. States are `CLOSED`, `OPEN`, `HALF_OPEN`; open breakers raise `CircuitOpenError`.

## 9. Settings

Secret/credential-bearing values are deliberately not reproduced. Defaults below are only shown when non-secret.

| Settings field | Type | Default |
|---|---|---|
| `db_url` | `str` | Credential-bearing connection URL; withheld |
| `redis_url` | `str` | `redis://localhost:6379/0` |
| `gemini_api_key` | `SecretStr \| None` | `None` |
| `llm_provider` | `str` | `"gemini"` |
| `fallback_llm_provider` | `str \| None` | `None` |
| `gemini_model` | `str` | `"gemini-2.0-flash"` |
| `openai_base_url` | `str \| None` | `None` |
| `openai_api_key` | `SecretStr \| None` | `None` |
| `openai_model` | `str` | `"gpt-4o-mini"` |
| `gemini_embedding_model` | `str` | `"gemini-embedding-001"` |
| `openai_embedding_model` | `str` | `"text-embedding-3-small"` |
| `simple_model` | `str \| None` | `None` |
| `supervisor_model` | `str \| None` | `None` |
| `itinerary_model` | `str \| None` | `None` |
| `llm_timeout_s` | `float` | `45.0` |
| `llm_retries` | `int` | `2` |
| `llm_retry_base_s` | `float` | `0.5` |
| `llm_input_cost_per_million_usd` | `float` | `0.0` |
| `llm_output_cost_per_million_usd` | `float` | `0.0` |
| `embedding_dimensions` | `int` | `768` (validator rejects other values) |
| `use_fake_providers` | `bool` | `False` |
| `google_maps_api_key` | `SecretStr \| None` | `None` |
| `weather_api_key` | `SecretStr \| None` | `None` |
| `places_cache_ttl_s` | `int` | `86400` |
| `geocoding_cache_ttl_s` | `int` | `2592000` |
| `weather_cache_ttl_s` | `int` | `3600` |
| `routes_cache_ttl_s` | `int` | `21600` |
| `tool_timeout_s` | `float` | `15.0` |
| `tool_retries` | `int` | `2` |
| `provider_rate_limit_per_s` | `float` | `5.0` |
| `provider_rate_limit_burst` | `int` | `10` |
| `circuit_breaker_failure_threshold` | `int` | `5` |
| `circuit_breaker_reset_s` | `float` | `30.0` |
| `fare_config_path` | `str` | `"app/tools/fare_estimates.json"` |
| `openweather_base_url` | `str` | `"https://api.openweathermap.org/data/2.5"` |
| `jwt_secret` | `SecretStr` | A development placeholder is configured; value withheld |
| `environment` | `str` | `"development"` |
| `log_level` | `str` | `"INFO"` |
| `cors_origins` | `str` | `"http://localhost:3000"` |

### Environment variables passed by Compose

Backend service mappings: `DB_URL`, `REDIS_URL`, `GEMINI_API_KEY`, `LLM_PROVIDER`, `FALLBACK_LLM_PROVIDER`, `GEMINI_MODEL`, `GEMINI_EMBEDDING_MODEL`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_EMBEDDING_MODEL`, `SIMPLE_MODEL`, `SUPERVISOR_MODEL`, `ITINERARY_MODEL`, `LLM_TIMEOUT_S`, `LLM_RETRIES`, `LLM_RETRY_BASE_S`, `LLM_INPUT_COST_PER_MILLION_USD`, `LLM_OUTPUT_COST_PER_MILLION_USD`, `EMBEDDING_DIMENSIONS`, `USE_FAKE_PROVIDERS`, `GOOGLE_MAPS_API_KEY`, `WEATHER_API_KEY`, `PLACES_CACHE_TTL_S`, `GEOCODING_CACHE_TTL_S`, `WEATHER_CACHE_TTL_S`, `ROUTES_CACHE_TTL_S`, `TOOL_TIMEOUT_S`, `TOOL_RETRIES`, `PROVIDER_RATE_LIMIT_PER_S`, `PROVIDER_RATE_LIMIT_BURST`, `CIRCUIT_BREAKER_FAILURE_THRESHOLD`, `CIRCUIT_BREAKER_RESET_S`, `FARE_CONFIG_PATH`, `OPENWEATHER_BASE_URL`, `JWT_SECRET`, `ENVIRONMENT`, and `LOG_LEVEL`.

PostgreSQL services also receive `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD`. Compose does not pass `CORS_ORIGINS` or `TEST_DATABASE_URL`; there is no `env_file:` declaration.

## 10. Dependencies

`pyproject.toml` specifies supported version ranges, not an exact lockfile. Runtime versions below were read from the running backend container.

| Package | `pyproject.toml` constraint | Installed |
|---|---|---|
| FastAPI | `>=0.110,<1` | `0.162.2` |
| Pydantic (transitive) | v2 used by source; no direct project pin | `2.14.3` |
| pydantic-settings | `>=2.2,<3` | `2.16.2` |
| email-validator | `>=2.1,<3` | `2.3.0` |
| SQLAlchemy asyncio | `>=2.0,<3` | `2.1.8` |
| Alembic | `>=1.13,<2` | `1.20.1` |
| asyncpg | `>=0.29,<1` | `0.32.0` |
| pgvector | `>=0.3,<1` | `0.5.1` |
| Redis | `>=5,<7` | `6.4.0` |
| structlog | `>=24.1,<26` | `25.5.0` |
| HTTPX | `>=0.27,<1` | `0.28.1` |
| uvicorn[standard] | `>=0.29,<1` | `0.54.0` |
| google-genai | `>=1.0,<2` | `1.75.0` |
| openai | `>=1.30,<3` | `2.54.0` |
| langchain-core | `>=0.2,<2` | `1.7.6` |
| langgraph | `>=0.2,<2` | `1.2.20` |
| pytest (dev) | `>=8.2,<9` | `8.4.2` |
| Ruff (dev) | `>=0.5,<1` | `0.17.4` |
| mypy (dev) | `>=1.10,<2` | `1.20.2` |

**Both `langgraph` and `langchain-core` are present** in declared dependencies and in the installed backend environment. Provider implementations currently use HTTPX REST interfaces rather than the SDK response objects.

## 11. Tests and requested checks

All commands were run inside the backend container:

| Command | Actual result |
|---|---|
| `docker compose exec -e TEST_DATABASE_URL=… backend pytest -q` | `27 passed in 10.45s` |
| `docker compose exec backend ruff check .` | `All checks passed!` |
| `docker compose exec backend mypy app` | `Success: no issues found in 59 source files` |
| `docker compose exec backend alembic check` | `No new upgrade operations detected.` |

The first attempt was unable to run because the backend service was stopped; after starting the Compose backend and its dependencies, the exact checks completed successfully.

Test files and coverage:

- `tests/test_config.py` — Settings environment coercion, boolean/numeric values, and fixed embedding dimension.
- `tests/test_health.py` — `/health` and `/version`.
- `tests/test_models.py` — PostgreSQL model relations/cascades, database constraints, repository CRUD/versioning/vector search, trip validation, and approval schemas.
- `tests/test_phase3_providers.py` — Mocked REST behavior for Gemini and OpenAI-compatible generation, JSON mode, embeddings, and streaming.
- `tests/test_phase3_registry.py` — Fake tool registry names/specs and LangChain invocation.
- `tests/test_phase3_resilience.py` — Transient retry filtering, circuit transitions, token-bucket throttling, structured-output repair, provider fallback, model role override, and usage tracking.
- `tests/test_phase3_tools.py` — Tool cache/lock behavior; mocked Places, geocoding, route and matrix normalization; weather primary/fallback/risk behavior; heuristic fare estimate labeling.
- `tests/conftest.py` — Creates the vector extension and model tables for each database test, then drops all model tables during teardown.

## 12. Known issues and risks

- No Phase 4 graph, agent implementation, application service, or tool/LLM API route exists yet; `app/orchestration`, `app/agents`, `app/services`, and `app/api` have no implementation beyond package initializers. Phase 4 must add the workflow and wire the existing contracts, persistence, LLM router, and tools.
- `SupervisorPlan`/`TaskSpec` do not validate task-key uniqueness, dependency references, or DAG acyclicity. Phase 4 must establish and test those invariants before graph compilation/execution.
- The Compose test command depends on a test database in the existing PostgreSQL volume. The current instance has `travelmind_test`, which is why the requested tests passed. On a fresh Compose volume the default `postgres` service initializes only its configured application database; `postgres-test` is under a `test` profile and is a different hostname. `TEST_DATABASE_URL` is not set by Compose. A clean setup needs explicit test DB creation/service selection and a matching test URL.
- `tests/conftest.py` drops all model tables from its configured test database after each fixture. Pointing `TEST_DATABASE_URL` at a non-disposable database risks data loss.
- Compose has no `env_file:` entry. Settings declares `.env` loading, but Compose only injects its explicit environment mappings; `CORS_ORIGINS` is absent from that mapping. `.env` is ignored by Git, but that does not make production secret provisioning automatic.
- `jwt_secret` has a hard-coded development placeholder in Settings/Compose. Compose also supplies local default database credentials. Their values are intentionally omitted here; all must be replaced for deployment.
- The configured JWT secret currently has no corresponding authentication implementation in the app.
- LLM streaming does not apply retries/circuit breakers and cannot choose a role-specific model through `LLMRouter.stream`.
- Cache lifecycle and provider HTTP client cleanup helpers exist, but the FastAPI lifespan does not close these shared cache/tool/provider resources; current app startup does not instantiate the tool registry or LLM factory.
- The configured provider SDK packages are declared and installed, but current provider adapters are HTTPX REST implementations. No build/lint/type/test failures were observed in the requested checks.
- Source search found no TODO/FIXME markers in the application or tests. Abstract `NotImplementedError` methods are base-class contracts, not concrete stubs. Fake tools intentionally return placeholders/zero values and must not be used as live travel data.
- The working tree already contains an uncommitted change to the domain migration file: the diff removes one trailing stray `h` at EOF. It does not alter migration operations. This audit did not edit or revert it.

## 13. Conventions

The current `.github/copilot-instructions.md` directs the project to use async-first FastAPI/Pydantic v2/SQLAlchemy async/PostgreSQL+pgvector/Redis; keep API schemas distinct from ORM models; validate agent I/O with Pydantic; use UUIDs, timezone-aware timestamps, JSONB, vector dimensions of 768, explicit FK deletion rules, reversible migrations, and repositories without business workflows/transaction ownership. It requires typed external tools, normalized provider results, structured logging without secrets, bounded retries and configurable resilience, tests for meaningful behavior, and explicit human approval for booking/payment. Phase boundaries reserve orchestration for Phase 4 and agents for Phase 5. Phase 3-specific conventions require routing model calls through `LLMRouter`, at most two structured-output repairs, clearly labeled/source-attributed heuristic prices, and no Phase 4/5 implementation in Phase 3.

## 14. Git state

`git status --short`:

```text
 M backend/alembic/versions/4e0af42457f6_create_domain_data_schema.py
?? docs/
```

The only new file under `docs/` is this report, `docs/PROJECT_STATE.md`.

`git log --oneline -5` (the repository currently has three commits):

```text
5b211aa Implement Phase 3 LLM and tool integrations
d1d5122 Add Phase 2 domain data layer
998e164 Initialize TravelMind Phase 1 foundation
```

`git check-ignore -v .env`:

```text
.gitignore:2:.env    .env
```

`.env` is gitignored.

## Ready for Phase 4?

- [ ] Decide and document the test-database provisioning/hostname workflow for clean Compose environments.
- [ ] Keep Phase 4 graph inputs validated: enforce unique task IDs, valid dependencies, and DAG acyclicity.
- [ ] Wire the router, registry, persistence, and approval checks into explicit application services; do not allow booking/payment execution without explicit approval.
- [ ] Define whether role-level model selection is sufficient or whether arbitrary agent-name mapping is required.
- [ ] Replace development secret/database defaults with deployment-safe secret provisioning before deployment.
- [ ] Add lifecycle ownership/cleanup for shared Redis and provider clients when they are wired into the app.
