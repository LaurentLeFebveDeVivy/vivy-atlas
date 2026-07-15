COMPOSE ?= podman-compose
HOST ?= 127.0.0.1
PORT ?= 5433
DB_USER ?= vivy
DB_NAME ?= vivyatlas
DB_URL ?= postgres://vivy:vivy@127.0.0.1:5433/vivyatlas?sslmode=disable

.PHONY: up down

up: 
	$(COMPOSE) up -d

down: 
	$(COMPOSE) down

inspect-pg:
	psql -h $(HOST) -p $(PORT) -U $(DB_USER) -d $(DB_NAME)

migrate:
	migrate -path migrations -database "$(DB_URL)" up