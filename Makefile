.PHONY: up down test logs
up:
	docker compose up --build -d
down:
	docker compose down
logs:
	docker compose logs -f backend worker
test:
	cd backend && ../.venv/bin/pytest -q
	cd frontend && npm run typecheck
