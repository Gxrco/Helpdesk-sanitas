.PHONY: init install start stop status configure demo validate test backup
init install start stop status configure demo validate backup:
	python3 scripts/sanitas.py $@
test:
	python3 -m unittest discover -s tests -v
