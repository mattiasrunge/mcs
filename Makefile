SRC = $(shell git ls-files \*.js)
DEFAULT_FLAGS := --reporter spec --ui tdd --recursive test
DEPS := deps

all: test lint style

deps:
	npm set progress=false
	npm --cache ./node_modules/.npm-cache install

test: $(DEPS)
	wget http://lorempixel.com/350/150 -O ./test/data/image1.jpg
	wget http://www.sample-videos.com/video/mp4/240/big_buck_bunny_240p_1mb.mp4 -O ./test/data/video1.mp4
	wget http://www.sample-videos.com/audio/mp3/crowd-cheering.mp3 -O ./test/data/audio1.mp3
	./node_modules/.bin/mocha $(DEFAULT_FLAGS)
	rm -rf ./test/data/image1.jpg
	rm -rf ./test/data/video1.mp4
	rm -rf ./test/data/audio1.mp3

lint: $(DEPS)
	./node_modules/.bin/jshint --verbose $(SRC)

style: $(DEPS)
	./node_modules/.bin/jscs -e --verbose $(SRC)


.PHONY: all deps test lint style
