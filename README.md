
# MCS - Media Cache Server
A server to transform video, images and audio files into versions suitable for the web. It also gets metadata for media files and runs face detection on images.

## CI status
[![Build Status](https://travis-ci.org/mattiasrunge/mcs.png)](https://travis-ci.org/mattiasrunge/mcs)

## Installation
```bash
git clone https://github.com/mattiasrunge/mcs
cd mcs
docker build . -t mcs
```

## Tests
```bash
docker run -it mcs npm test
```

## Run
```bash
docker run -p 8181:8181 --name mcs -v /data/cache:/data/cache -v /data/files:/data/files -it mcs
```
Default access key is: **let-me-in**
