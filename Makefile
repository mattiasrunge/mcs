SRC = $(shell git ls-files \*.js)
DEFAULT_FLAGS := --reporter spec --ui tdd --recursive test
DEPS := deps

IMAGE := ./test/data/image1.jpg
VIDEO := ./test/data/video1.mp4
AUDIO := ./test/data/audio1.mp3

all: test lint style

deps:
	npm set progress=false
	npm install

$(IMAGE):
	wget http://lorempixel.com/350/150 -O $@

$(VIDEO):
	wget http://www.sample-videos.com/video/mp4/240/big_buck_bunny_240p_1mb.mp4 -O $@

$(AUDIO):
	wget http://www.sample-videos.com/audio/mp3/crowd-cheering.mp3 -O $@

test: $(DEPS) $(IMAGE) $(VIDEO) $(AUDIO)
	./node_modules/.bin/mocha $(DEFAULT_FLAGS)

lint: $(DEPS)
	./node_modules/.bin/jshint --verbose $(SRC)

style: $(DEPS)
	./node_modules/.bin/jscs -e --verbose $(SRC)


.PHONY: all deps test lint style
