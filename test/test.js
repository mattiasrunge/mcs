"use strict";

const path = require("path");
const mocha = require("mocha");
const co = require("co");
const uuid = require("node-uuid");
const getPort = require("get-port");
const fs = require("fs-extra-promise");
const assert = require("chai").assert;
const api = require("api.io").client;
const main = require("../lib/main");
const configuration = require("../lib/configuration");
const file = require("../lib/file");
const utils = require("../lib/utils");

// Create mocha-functions which deals with generators
function mochaGen(originalFn) {
    return (text, fn) => {
        fn = typeof text === "function" ? text : fn;

        if (fn.constructor.name === "GeneratorFunction") {
            let oldFn = fn;
            fn = (done) => {
                co.wrap(oldFn)()
                .then(done)
                .catch(done);
            };
        }

        if (typeof text === "function") {
            originalFn(fn);
        } else {
            originalFn(text, fn);
        }
    };
}

// Override mocha, we get W020 lint warning which we ignore since it works...
it = mochaGen(mocha.it); // jshint ignore:line
before = mochaGen(mocha.before); // jshint ignore:line
after = mochaGen(mocha.after); // jshint ignore:line

describe("Test", function() {
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

    describe("Setup", function() {
        it("should connect", function*() {
            yield api.connect({
                hostname: "localhost",
                port: configuration.port
            });
        });

        it("should authenticate", function*() {
            let result = yield api.cache.authenticate("secret key");

            assert.ok(result);
        });
    });

    describe("File", function() {
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
    });

    describe("Image2Image", function() {
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

    describe("Video2Image", function() {
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

    describe("Video2Video", function() {
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

    describe("Audio2Audio", function() {
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
});
