.PHONY: run clean test help

# Default target
help:
	@echo "JSON Event Log Processor - Available targets:"
	@echo "  run      - Process the committed sample data"
	@echo "  clean    - Remove results, database, and inbox files"
	@echo "  test     - Run all tests"
	@echo "  sample   - Copy sample data to inbox"

# Process the committed sample data.
run:
	python main.py

# Copy sample data to a mutable inbox for a separately configured run.
# A *.json inbox entry is a complete, published input, so each file is written
# under a non-.json staging name and renamed into place once it is whole.
sample:
	@echo "Staging sample data, then renaming it into inbox/..."
	mkdir -p inbox
	for f in sample_data/*.json; do n=$${f##*/}; cp "$$f" "inbox/$$n.tmp" && mv "inbox/$$n.tmp" "inbox/$$n"; done
	@echo "Done. Set inbox_dir = \"inbox\" in config.toml to process that copy."

# Remove generated files
clean:
	@echo "Cleaning results/, rpacore.db, and inbox/..."
	rm -rf results/ rpacore.db
	rm -f inbox/*.json inbox/*.tmp
	@echo "Clean complete."

# Run all tests
test:
	python -m pytest tests/ -v
