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
	docker compose exec backend pytest

lint:
	docker compose exec backend ruff check .

format:
	docker compose exec backend ruff format .
