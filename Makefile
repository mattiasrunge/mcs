SRC = $(shell git ls-files \*.js)
DEFAULT_FLAGS := --reporter spec --ui tdd --recursive test
DEPS := deps

all: test lint style

deps:
	npm set progress=false
	npm --cache ./node_modules/.npm-cache install

test: $(DEPS)
	wget -q -k https://placehold.it/350x150 -O ./test/data/image1.png
	./node_modules/.bin/mocha $(DEFAULT_FLAGS)

lint: $(DEPS)
	./node_modules/.bin/jshint --verbose $(SRC)

style: $(DEPS)
	./node_modules/.bin/jscs -e --verbose $(SRC)


.PHONY: all deps test lint style
