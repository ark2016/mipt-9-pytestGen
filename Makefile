# Thin aliases; everything real lives in docker-compose.yml and pyproject.toml.
.PHONY: demo demo-3b llm down clean-models test slow

# One command: build the image, start the model server, generate tests for examples/shop/pricing.py.
demo:
	docker compose run --rm --build pytestgen

demo-3b:
	docker compose --env-file scripts/llm-3b.env run --rm --build pytestgen

# Only the model server (OpenAI-compatible API on http://localhost:8080/v1).
llm:
	docker compose up -d --wait llm

down:
	docker compose down

# Also forget the downloaded model.
clean-models:
	docker compose down -v

test:
	uv run pytest -m "not slow"

# Needs a running server: make llm && make slow
slow:
	PYTESTGEN_LLM_BASE_URL=http://localhost:8080/v1 PYTESTGEN_LLM_MODEL=qwen2.5-coder-1.5b-instruct \
	uv run pytest -m slow -v
