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

describe("Cache", function() {
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

    describe("Image2Image", () => {
        it("should resize a file with only width set", function*() {
            let filename = path.resolve(__dirname, "data/image1.jpg");
            let id = uuid.v4();

            let result = yield api.cache.get(id, filename, {
                type: "image",
                width: 20
            });

            let exists = yield fs.existsAsync(result);
            assert.isOk(exists);

            let size = yield file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 9);
        });

        it("should resize a file with width and height set", function*() {
            let filename = path.resolve(__dirname, "data/image1.jpg");
            let id = uuid.v4();

            let result = yield api.cache.get(id, filename, {
                type: "image",
                width: 20,
                height: 30
            });

            let exists = yield fs.existsAsync(result);
            assert.isOk(exists);

            let size = yield file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 30);
        });

        it("should resize a file with width and height equal", function*() {
            let filename = path.resolve(__dirname, "data/image1.jpg");
            let id = uuid.v4();

            let result = yield api.cache.get(id, filename, {
                type: "image",
                width: 20,
                height: 20
            });

            let exists = yield fs.existsAsync(result);
            assert.isOk(exists);

            let size = yield file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 20);
        });

        it("should rotate a file 90 degrees", function*() {
            let filename = path.resolve(__dirname, "data/image1.jpg");
            let id = uuid.v4();

            let result = yield api.cache.get(id, filename, {
                type: "image",
                angle: 90
            });

            let exists = yield fs.existsAsync(result);
            assert.isOk(exists);

            let size = yield file.getSize(result);
            assert.equal(size.width, 150);
            assert.equal(size.height, 350);
        });

        it("should mirror a file", function*() {
            let filename = path.resolve(__dirname, "data/image1.jpg");
            let id = uuid.v4();

            let result = yield api.cache.get(id, filename, {
                type: "image",
                mirror: true
            });

            let exists = yield fs.existsAsync(result);
            assert.isOk(exists);

            let size = yield file.getSize(result);
            assert.equal(size.width, 350);
            assert.equal(size.height, 150);
        });

        it("should rotate and resize a file", function*() {
            let filename = path.resolve(__dirname, "data/image1.jpg");
            let id = uuid.v4();

            let result = yield api.cache.get(id, filename, {
                type: "image",
                angle: 90,
                width: 75,
                height: 175
            });

            let exists = yield fs.existsAsync(result);
            assert.isOk(exists);

            let size = yield file.getSize(result);
            assert.equal(size.width, 75);
            assert.equal(size.height, 175);
        });
    });

    describe("Video2Image", () => {
        it("should resize a file with only width set", function*() {
            let filename = path.resolve(__dirname, "data/video1.mp4");
            let id = uuid.v4();

            let result = yield api.cache.get(id, filename, {
                type: "image",
                width: 20
            });

            let exists = yield fs.existsAsync(result);
            assert.isOk(exists);

            let size = yield file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 15);
        });

        it("should resize a file with width and height set", function*() {
            let filename = path.resolve(__dirname, "data/video1.mp4");
            let id = uuid.v4();

            let result = yield api.cache.get(id, filename, {
                type: "image",
                width: 20,
                height: 30
            });

            let exists = yield fs.existsAsync(result);
            assert.isOk(exists);

            let size = yield file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 30);
        });

        it("should resize a file with width and height equal", function*() {
            let filename = path.resolve(__dirname, "data/video1.mp4");
            let id = uuid.v4();

            let result = yield api.cache.get(id, filename, {
                type: "image",
                width: 20,
                height: 20
            });

            let exists = yield fs.existsAsync(result);
            assert.isOk(exists);

            let size = yield file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 20);
        });
    });

    describe("Video2Video", () => {
        it("should convert a file", function*() {
            let filename = path.resolve(__dirname, "data/video1.mp4");
            let id = uuid.v4();

            let result = yield api.cache.get(id, filename, {
                type: "video"
            });

            let exists = yield fs.existsAsync(result);
            assert.isOk(exists);
        });

        it("should resize a file with only width set", function*() {
            let filename = path.resolve(__dirname, "data/video1.mp4");
            let id = uuid.v4();

            let result = yield api.cache.get(id, filename, {
                type: "video",
                width: 20
            });

            let exists = yield fs.existsAsync(result);
            assert.isOk(exists);

            let size = yield file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 15);
        });

        it("should resize a file with width and height set", function*() {
            let filename = path.resolve(__dirname, "data/video1.mp4");
            let id = uuid.v4();

            let result = yield api.cache.get(id, filename, {
                type: "video",
                width: 20,
                height: 30
            });

            let exists = yield fs.existsAsync(result);
            assert.isOk(exists);

            let size = yield file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 30);
        });

        it("should resize a file with width and height equal", function*() {
            let filename = path.resolve(__dirname, "data/video1.mp4");
            let id = uuid.v4();

            let result = yield api.cache.get(id, filename, {
                type: "video",
                width: 20,
                height: 20
            });

            let exists = yield fs.existsAsync(result);
            assert.isOk(exists);

            let size = yield file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 20);
        });
    });

    describe("Audio2Audio", () => {
        it("should convert a file", function*() {
            let filename = path.resolve(__dirname, "data/audio1.mp3");
            let id = uuid.v4();

            let result = yield api.cache.get(id, filename, {
                type: "audio"
            });

            let exists = yield fs.existsAsync(result);
            assert.isOk(exists);
        });
    });

    describe("Remove", () => {
        it("should create two files and then remove them", function*() {
            let filename = path.resolve(__dirname, "data/image1.jpg");
            let id = uuid.v4();

            let result1 = yield api.cache.get(id, filename, {
                type: "image",
                width: 20
            });

            let result2 = yield api.cache.get(id, filename, {
                type: "image",
                width: 30
            });

            assert.isOk(yield fs.existsAsync(result1));
            assert.isOk(yield fs.existsAsync(result2));

            let result = yield api.cache.remove([ id ]);

            assert.equal(result, 2);

            assert.isNotOk(yield fs.existsAsync(result1));
            assert.isNotOk(yield fs.existsAsync(result2));
        });
    });
});
