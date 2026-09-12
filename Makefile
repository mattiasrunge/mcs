# MCS v2. `make help` lists the targets.
#
# Local: the venv runs the front's tests (no models, no tools needed beyond what the tests
# skip without). Container: the real thing, built and run here or on a remote host through
# deploy/remote.sh with a profile from deploy/hosts/<host>.env.

.DEFAULT_GOAL := help
SHELL := /bin/bash

VENV        ?= .venv
PY          := $(VENV)/bin/python
IMAGE_NAME  ?= mcs
CONTAINER_NAME ?= mcs
HOST        ?= fry

# Build arguments. These MUST match the MURRiX image's values for the shared layer prefix to
# hit podman's cache (see the note at the top of Containerfile); MURRiX's Makefile is the
# source of the defaults.
TORCH_CUDA      ?= cu130
INSTRUCT_MODEL  ?= Qwen/Qwen2.5-1.5B-Instruct
VLM_MODEL       ?= Qwen/Qwen3-VL-8B-Instruct
WHISPER_MODEL   ?= large-v3
FFMPEG_BUILD    ?= autobuild-2026-08-31-13-27
FFMPEG_ASSET    ?= ffmpeg-n9.0.1-11-ge47273f4d9-linux64-gpl-9.0

# Run arguments. Roots are mounted at the same path inside as outside so the paths MURRiX
# resolves are the paths MCS opens; MCS_ROOTS names what it may touch.
MCS_PORT        ?= 8181
MCS_KEY         ?= let-me-in
FILES_PATH      ?= /srv/files
OLD_PATH        ?= /srv/old
VOLATILE_PATH   ?= /srv/files-volatile
TMP_PATH        ?= /srv/files-tmp
MCS_MEMORY      ?= 16g
GPU             ?=
GPU_FLAG        := $(if $(GPU),--device nvidia.com/gpu=all,)

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- local ---

venv: ## Create the dev venv (python3-venv without ensurepip is fine; pip bootstraps it)
	python3 -m venv --without-pip $(VENV)
	pip3 --python $(PY) install --disable-pip-version-check -e '.[test]' 2>/dev/null || pip3 --python $(PY) install --disable-pip-version-check fastapi 'uvicorn[standard]' httpx pytest pytest-asyncio

test: ## Run the front's tests
	$(PY) -m pytest -q

serve: ## Serve locally from the venv (no models; MCS_ROOTS and MCS_KEY from the environment)
	$(PY) -m mcs

# --- container ---

build: ## Build the image (TMPDIR on the graph root: the model layers are tens of GB)
	TMPDIR="$$(podman info --format '{{.Store.GraphRoot}}')/tmp"; mkdir -p "$$TMPDIR"; \
	TMPDIR="$$TMPDIR" podman build -t $(IMAGE_NAME) -f Containerfile \
		--build-arg TORCH_CUDA=$(TORCH_CUDA) \
		--build-arg INSTRUCT_MODEL=$(INSTRUCT_MODEL) \
		--build-arg VLM_MODEL=$(VLM_MODEL) \
		--build-arg WHISPER_MODEL=$(WHISPER_MODEL) \
		--build-arg FFMPEG_BUILD=$(FFMPEG_BUILD) \
		--build-arg FFMPEG_ASSET=$(FFMPEG_ASSET) \
		.

run: ## Run the container (GPU=1 for NVIDIA; roots from FILES_PATH/OLD_PATH/VOLATILE_PATH/TMP_PATH)
	@mkdir -p $(VOLATILE_PATH) $(TMP_PATH)
	podman run --replace -d --name $(CONTAINER_NAME) --restart on-failure:5 --stop-timeout 60 \
		--memory $(MCS_MEMORY) --memory-swap $(MCS_MEMORY) \
		$(GPU_FLAG) --network=host \
		-e MCS_PORT=$(MCS_PORT) -e MCS_KEY=$(MCS_KEY) \
		-e MCS_ROOTS=$(FILES_PATH):ro,$(OLD_PATH):ro,$(VOLATILE_PATH):rw,$(TMP_PATH):rw \
		$(if $(MCS_MODEL_PINNED),-e MCS_MODEL_PINNED=$(MCS_MODEL_PINNED),) \
		$(if $(MCS_MODEL_IDLE_EVICT),-e MCS_MODEL_IDLE_EVICT=$(MCS_MODEL_IDLE_EVICT),) \
		$(if $(MCS_MODEL_MAX_RSS),-e MCS_MODEL_MAX_RSS=$(MCS_MODEL_MAX_RSS),) \
		$(if $(MCS_INSTRUCT_DEVICE),-e MCS_INSTRUCT_DEVICE=$(MCS_INSTRUCT_DEVICE),) \
		$(if $(MCS_VLM_QUANT),-e MCS_VLM_QUANT=$(MCS_VLM_QUANT),) \
		$(if $(MCS_WHISPER_LANGUAGE),-e MCS_WHISPER_LANGUAGE=$(MCS_WHISPER_LANGUAGE),) \
		$(if $(TZ),-e TZ=$(TZ),) \
		-v $(FILES_PATH):$(FILES_PATH):ro \
		-v $(OLD_PATH):$(OLD_PATH):ro \
		-v $(VOLATILE_PATH):$(VOLATILE_PATH) \
		-v $(TMP_PATH):$(TMP_PATH) \
		$(IMAGE_NAME)

stop: ## Stop the container
	podman stop -t 60 $(CONTAINER_NAME)

logs: ## Tail the container's log
	podman logs -f $(CONTAINER_NAME)

shell: ## A bash shell inside the running container
	podman exec -it $(CONTAINER_NAME) bash

gpu-check: ## Real inference on every model inside the running container (what device each bound to)
	podman exec $(CONTAINER_NAME) python3 scripts/gpu_selftest.py

health: ## Ask the running MCS how it is
	@curl -s -H "Authorization: Bearer $(MCS_KEY)" http://localhost:$(MCS_PORT)/v2/health | python3 -m json.tool

# --- remote (deploy/hosts/<HOST>.env) ---

remote-%: ## Run a verb on HOST: sync build run stop restart logs status shell gpu-check health
	bash deploy/remote.sh $(HOST) $*

.PHONY: help venv test serve build run stop logs shell gpu-check health
