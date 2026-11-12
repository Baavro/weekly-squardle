.PHONY: run test docker
run:
	uvicorn backend.main:app --reload --port 8000

test:
	python -m unittest discover -s tests -v

docker:
	docker build -t weekly-squardle .
