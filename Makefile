SRC = $(shell git ls-files \*.js)
DEFAULT_FLAGS := --reporter spec --ui tdd --recursive test
DEPS := deps

all: test lint style

waveform:
	gcc -I/usr/local/include/ffmpeg -L/usr/local/lib/ffmpeg -I/usr/local/include -L/usr/local/lib -o ./bin/waveform ./src/main.c -Wall -g -O3 -lavcodec -lavutil -lavformat -lpng -lm

req:
	sudo apt-get install -y libimage-exiftool-perl libav-tools imagemagick file ufraw-batch libopencv-dev libpng-dev g++ gcc unoconv

deps: waveform
	npm set progress=false
	npm install

test: $(DEPS)
	alias python="/usr/bin/python3"
	./node_modules/.bin/mocha $(DEFAULT_FLAGS)

lint: $(DEPS)
	./node_modules/.bin/jshint --verbose $(SRC)

style: $(DEPS)
	./node_modules/.bin/jscs -e --verbose $(SRC)


.PHONY: all req deps test lint style waveform
