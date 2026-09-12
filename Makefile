.PHONY: doctor build up collect-cli verify down clean test fault-test
doctor build up collect-cli verify down clean fault-test:
	uv run --frozen python scripts/lab.py $@
test:
	uv run --frozen pytest -q
