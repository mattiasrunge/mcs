"use strict";

const mocha = require("mocha");
const co = require("co");
const assert = require("chai").assert;
const main = require("../lib/main");
const configuration = require("../lib/configuration");
const api = require("api.io").client;
const getPort = require("get-port");
const tmp = require("tmp");
const fs = require("fs-extra-promise");
const uuid = require("node-uuid");
const path = require("path");
const file = require("../lib/file");

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
    this.timeout(10000);

    before(function*() {
        let tmpobj = tmp.dirSync();

        let args = {
            level: "debug",
            config: "test/data/config.json",
            cachePath: tmpobj.name,
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

    describe("Image2Image", function() {
        it("should resize a file with only width set", function*() {
            let filename = path.resolve(__dirname, "data/image1.png");
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
            let filename = path.resolve(__dirname, "data/image1.png");
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
            let filename = path.resolve(__dirname, "data/image1.png");
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
});
