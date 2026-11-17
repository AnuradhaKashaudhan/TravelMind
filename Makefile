.PHONY: up down logs migrate test lint format

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f backend postgres redis

migrate:
	docker compose exec backend alembic upgrade head

test:
	docker compose --profile test up -d postgres-test
	docker compose exec -T postgres-test sh -c 'until pg_isready -U "$$POSTGRES_USER" -d postgres >/dev/null; do sleep 1; done; psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -tAc "SELECT 1" >/dev/null 2>&1 || createdb -U "$$POSTGRES_USER" "$$POSTGRES_DB"'
	docker compose run --build --rm --no-deps backend pytest -q

lint:
	docker compose exec backend ruff check .

format:
	docker compose exec backend ruff format .
