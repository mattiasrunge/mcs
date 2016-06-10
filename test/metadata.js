"use strict";

const path = require("path");
const uuid = require("node-uuid");
const getPort = require("get-port");
const fs = require("fs-extra-promise");
const assert = require("chai").assert;
const api = require("api.io").client;
const main = require("../lib/main");
const configuration = require("../lib/configuration");
const file = require("../lib/file");
const utils = require("../lib/utils");

require("../lib/test");

describe("Metadata", function() {
    this.timeout(20000);

    before(function*() {
        let args = {
            level: "debug",
            config: "test/data/config.json",
            cachePath: yield utils.createTmpDir(),
            port: yield getPort(),
            keys: [ "secret key" ]
        };

        yield fs.ensureDirAsync(args.cachePath);

        yield main.start(args);
    });

    after(function*() {
        yield main.stop();

        yield fs.removeAsync(configuration.cachePath);
    });

    describe("Setup", () => {
        it("should connect", function*() {
            yield api.connect({
                hostname: "localhost",
                port: configuration.port
            });
        });

        it("should authenticate", function*() {
            let result = yield api.auth.identify("secret key");

            assert.ok(result);
        });
    });

    describe("File", () => {
        it("should return the size of a file", function*() {
            let filename = path.resolve(__dirname, "data/image1.jpg");
            let size = yield file.getSize(filename);

            assert.equal(size.width, 350);
            assert.equal(size.height, 150);
        });

        it("should return the mimetype of a file", function*() {
            let filename = path.resolve(__dirname, "data/image1.jpg");
            let mimetype = yield file.getMimetype(filename);

            assert.equal(mimetype, "image/jpeg");
        });

        it("should return the exif data of a file", function*() {
            let filename = path.resolve(__dirname, "data/image1.jpg");
            let exif = yield file.getExif(filename);

            assert.equal(exif.FileName, "image1.jpg");
            assert.equal(exif.FileType, "JPEG");
            assert.equal(exif.MIMEType, "image/jpeg");
            assert.equal(exif.ImageSize, "350x150");
        });

        it("should return the metadata for an image file", function*() {
            let filename = path.resolve(__dirname, "data/image1.jpg");
            let metadata = yield api.metadata.get(filename);

            assert.equal(metadata.name, "image1.jpg");
            assert.equal(metadata.type, "image");
            assert.equal(metadata.mimetype, "image/jpeg");
            assert.equal(metadata.width, 350);
            assert.equal(metadata.height, 150);
            assert.equal(metadata.rawImage, false);
        });

        it("should return the metadata for a video file", function*() {
            let filename = path.resolve(__dirname, "data/video1.mp4");
            let metadata = yield api.metadata.get(filename);

            assert.equal(metadata.name, "video1.mp4");
            assert.equal(metadata.type, "video");
            assert.equal(metadata.mimetype, "video/mp4");
            assert.equal(metadata.width, 320);
            assert.equal(metadata.height, 240);
        });

        it("should return the metadata for an audio file", function*() {
            let filename = path.resolve(__dirname, "data/audio1.mp3");
            let metadata = yield api.metadata.get(filename);

            assert.equal(metadata.name, "audio1.mp3");
            assert.equal(metadata.type, "audio");
            assert.equal(metadata.mimetype, "audio/mpeg");
        });
    });
});
