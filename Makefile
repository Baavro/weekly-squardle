.PHONY: install run test test-game
install:
	python -m pip install -r backend/requirements.txt
run:
	uvicorn backend.main:app --reload --port 8000
test:
	python -m pytest -q
test-game:
	SQUARDLE_TEST_MODE=1 uvicorn backend.main:app --reload --port 8000
