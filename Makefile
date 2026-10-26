IMAGE_NAME := korailbot:local
PY := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

help:           ## Show this help.
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//'

.PHONY: venv
venv:			## Create .venv with Python 3.12 and install dependencies
	python3.12 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt

.PHONY: install
install:		## Install dependencies into the current environment
	pip install -r requirements.txt

.PHONY: redis
redis:			## Start a local Redis (docker)
	docker compose up -d redis

.PHONY: status
status:			## Show reservation status
	$(PY) src/cli.py status

.PHONY: requirements
requirements:	## Generate requirements.txt from Pipfile.lock
	pipenv requirements > requirements.txt

.PHONY: test
test:			## Run all tests with pytest (needs Docker or a local Redis, see tests/README.md)
	$(PY) -m pytest tests/ -v

.PHONY: test-unit
test-unit:		## Run unit tests only
	$(PY) -m pytest tests/unit -v

.PHONY: test-integration
test-integration:	## Run integration tests only
	$(PY) -m pytest tests/integration -v

.PHONY: build
build:			## Build Docker Image
	docker build -t ${IMAGE_NAME} .
