SRC = $(shell git ls-files \*.js)
DEFAULT_FLAGS := --reporter spec --ui tdd --recursive test
DEPS := deps

all: test lint

req:
	sudo apt-get install -y libimage-exiftool-perl libav-tools imagemagick file ufraw-batch libpng-dev libavformat-dev g++ gcc unoconv libopenblas-dev cmake

deps:
	npm set progress=false
	npm install

test: $(DEPS)
	alias python="/usr/bin/python3"
	./node_modules/.bin/mocha $(DEFAULT_FLAGS)

lint: $(DEPS)
	./node_modules/.bin/eslint $(SRC)


.PHONY: all req deps test lint
