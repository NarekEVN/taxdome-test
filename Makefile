PYTHON ?= python3.11
VENV ?= .venv
BIN := $(VENV)/bin

.DEFAULT_GOAL := help
.PHONY: help install env up down reset test lint fmt run demo concurrency-demo

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: env ## Create the venv, install dev dependencies, create .env from .env.example
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -r requirements-dev.txt

env: ## Create .env from .env.example if it does not exist
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")

up: env ## Build and start Postgres, 2 app instances and nginx (http://localhost:8080)
	docker compose up -d --build --wait

down: ## Stop the stack (keeps the database volume)
	docker compose down

reset: ## Stop the stack, delete the database volume, start fresh
	docker compose down -v
	$(MAKE) up

test: ## Run all tests (needs the db container: make up)
	$(BIN)/pytest

lint: ## Check lint and formatting
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

fmt: ## Fix lint issues and format
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

run: ## Run one app instance locally with reload on http://localhost:8000
	$(BIN)/uvicorn --factory app.main:create_app --reload

demo: ## Reset data, send the assignment sample through nginx, check the balances
	scripts/demo.sh

concurrency-demo: ## Reset data, fire parallel payments at both instances, check nothing overdraws
	scripts/concurrency_demo.sh
