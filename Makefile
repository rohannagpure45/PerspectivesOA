UV ?= uv
HAR ?= /Users/rohan/Downloads/secure.simplepractice.com.har
HASHED ?= 0c39dadff6972e0f

.PHONY: install dev test lint typecheck migrate seed parse-har capture-har psql clean

install:
	$(UV) sync --all-extras

dev:
	$(UV) run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	$(UV) run pytest -q

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:
	$(UV) run ruff format .

typecheck:
	$(UV) run mypy app

migrate:
	$(UV) run alembic upgrade head

seed:
	$(UV) run python scripts/seed_db.py

parse-har:
	$(UV) run python scripts/parse_har.py "$(HAR)"

capture-har:
	@echo "1. Open Chrome DevTools (Cmd+Option+I) > Network tab"
	@echo "2. Click the gear icon, enable 'Preserve log'"
	@echo "3. Navigate to https://secure.simplepractice.com/clients/$(HASHED)/overview"
	@echo "4. Click around: overview, timeline, each appointment with a progress note"
	@echo "5. In Network tab, right-click any request -> Save all as HAR with content"
	@echo "6. Save to ~/Downloads/secure.simplepractice.com.har"
	@echo "7. Run: make parse-har"

psql:
	docker compose exec postgres psql -U perspectives -d perspectives_oa

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
