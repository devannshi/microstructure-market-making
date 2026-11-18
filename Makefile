.PHONY: install test quick research historical figures report reproduce lint
install:
	python -m pip install -e ".[dev]"
test:
	python -m pytest
quick:
	mm-lab research --quick --output results/smoke
research:
	mm-lab research --output results
historical:
	mm-lab historical --output results
figures:
	mm-lab figures --output results
report:
	mm-lab report --output results
reproduce: research historical figures report
lint:
	python -m ruff check src tests
