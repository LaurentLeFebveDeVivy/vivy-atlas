COMPOSE ?= podman-compose
HOST ?= 127.0.0.1
PORT ?= 5433
DB_USER ?= vivy
DB_NAME ?= vivyatlas
DB_URL ?= postgres://vivy:vivy@127.0.0.1:5433/vivyatlas?sslmode=disable
CONFIG_DIR := $(HOME)/.config/vivyatlas
BINDIR ?= /usr/local/bin


.PHONY: help up down inspect-pg migrate register sync sync-select install

.DEFAULT_GOAL := help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "} {printf "\033[36m%-12s\033[37m%s\033[0m\n", $$1, $$2}'

up: ## Start Postgres (pgvector) container, detached
	$(COMPOSE) up -d

down: ## Stop the container (data survives in the pgdata volume)
	$(COMPOSE) down

inspect-pg: ## Open a psql shell into the DB
	psql -h $(HOST) -p $(PORT) -U $(DB_USER) -d $(DB_NAME)

migrate: ## Apply pending migrations (golang-migrate)
	migrate -path migrations -database "$(DB_URL)" up

sync: ## Sync all active connector instances
	uv run python -m pipeline.sync

sync-select: ## Interactively pick which instances to sync
	uv run python -m pipeline.sync --select

register: ## Register a connector instance: make register type=<type> cp=<config_path>
ifndef type
	$(error usage: make register type=<connector_type> cp=<config_path>)
endif
ifndef cp
	$(error usage: make register type=<connector_type> cp=<config_path>)
endif
	uv run python -m pipeline.register $(type) $(cp)

install: ## Install the vivy CLI and symlink config into ~/.config
	cd server && go build -o /tmp/vivy ./cmd/vivy
	sudo install -m 0755 /tmp/vivy $(BINDIR)/vivy
	mkdir -p $(CONFIG_DIR)
	ln -sfn $(CURDIR)/config.yaml $(CONFIG_DIR)/config.yaml
