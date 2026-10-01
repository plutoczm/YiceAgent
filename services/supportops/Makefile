.PHONY: install test lint format run up down logs build check eval

install:
	python -m pip install -e ".[dev]"

lint:
	ruff check .

format:
	ruff format .

test:
	pytest --cov=app --cov-report=term-missing --cov-fail-under=75

run:
	uvicorn app.main:app --reload

up:
	docker compose --profile rag up --build

down:
	docker compose --profile rag down

logs:
	docker compose --profile rag logs -f api

build:
	docker build -t supportops-ai:local .

check:
	docker compose config -q
	docker compose --profile rag config -q
	python -m compileall app evals migrations tests


eval:
	python -m evals.run_evals
	python -m evals.run_retrieval_evals
	python -m evals.run_e2e_regression
