.PHONY: install run test test-game
install:
	python -m pip install -r backend/requirements.txt
run:
	uvicorn backend.main:app --reload --port 8000
test:
	python -m pytest -q
test-game:
	uvicorn backend.main:app --reload --port 8000  # then open http://localhost:8000/?test
