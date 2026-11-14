.PHONY: install test benchmark benchmark-quick report clean help

PYTHON ?= python
BENCHMARK_DIR := $(shell dirname $(realpath $(lastword $(MAKEFILE_LIST))))
PROJECT_ROOT := $(shell dirname $(BENCHMARK_DIR))

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install benchmark dependencies
	$(PYTHON) -m pip install -r $(BENCHMARK_DIR)/requirements.txt
	$(PYTHON) -m spacy download en_core_web_sm

test:  ## Run benchmark unit tests
	cd $(PROJECT_ROOT) && $(PYTHON) -m pytest benchmark/tests/ -v

benchmark:  ## Run full benchmark (all tools, all URLs)
	cd $(PROJECT_ROOT) && $(PYTHON) -m benchmark.runner

benchmark-quick:  ## Run benchmark with Phantombot only
	cd $(PROJECT_ROOT) && $(PYTHON) -m benchmark.runner --tools phantombot

benchmark-no-docker:  ## Run benchmark without Docker-dependent tools
	cd $(PROJECT_ROOT) && $(PYTHON) -m benchmark.runner --tools phantombot,crawl4ai

report:  ## Generate report from latest results
	@echo "Use: $(PYTHON) -m benchmark.report <path-to-raw_results.json>"

clean:  ## Remove benchmark results
	rm -rf $(BENCHMARK_DIR)/results/*/
