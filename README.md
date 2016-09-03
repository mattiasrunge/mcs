# MCS - Media Cache Server
A server to transform video, images and audio files into versions suitable for the web.


## Tests
[![Build Status](https://travis-ci.org/mattiasrunge/mcs.png)](https://travis-ci.org/mattiasrunge/mcs)

## Installation
MCS requires a few tools that it uses to get image metadata, resize images etc. So some things must be installed in the system beforehand.
This will require a newer Ubuntu (or derivative) than trusty (14.04).

```bash
sudo apt-get install libimage-exiftool-perl libav-tools imagemagick file ufraw-batch libopencv-dev libpng-dev g++ gcc unoconv

git clone https://github.com/mattiasrunge/mcs

cd mcs

make deps
```

## Configuration
MCS uses a configuration file. There is a sample file available that can be used as a template.
```bash
cp conf/config.json.sample conf/config.json
```

The default configuration looks like this.
```json
{
  "port": 8181,
  "keys": [ "let-me-in" ]
}
```

* port is the HTTP port where the MCS will be available to clients
* keys is a list of keys that clients can authenticate with

## Run
```bash
./bin/mcs
```
