.DEFAULT_GOAL := help

UV := uv
PYTHON_VERSION := 3.14

.PHONY: sync lock upgrade format lint check test build

sync:
	$(UV) sync --python $(PYTHON_VERSION) --locked

lint:
	@status=0; \
	$(UV) run ruff format . || status=1; \
	$(UV) run ruff check --fix . || status=1; \
	$(UV) run ty check || status=1; \
	exit $$status

check:
	@status=0; \
	$(UV) run ruff format --check . || status=1; \
	$(UV) run ruff check . || status=1; \
	$(UV) run ty check || status=1; \
	exit $$status

test:
	$(UV) run pytest

test_all:
	hatch test --all

# test_py v=3.10
test_py:
	hatch test -py $(v)

build:
	$(UV) run hatch build

push_tag:
	VERSION="$(uv version --short)"
	git tag -a "v${VERSION}" -m "runpoint v${VERSION}"
	git push origin "v${VERSION}"

bump_patch:
	git switch main && git pull
	uv version --bump patch && uv lock
	make lint && make test && git add . && git commit -m "bump version" && git push

bump_minor:
	git switch main && git pull
	uv version --bump minor && uv lock
	make lint && make test && git add . && git commit -m "bump version" && git push

