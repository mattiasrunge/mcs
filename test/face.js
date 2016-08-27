"use strict";

const getPort = require("get-port");
const chai = require("chai");
const api = require("api.io").client;
const main = require("../lib/main");
const configuration = require("../lib/configuration");

require("../lib/test");
const assert = chai.assert;

describe("Face", function() {
    this.timeout(20000);

    before(function*() {
        let args = {
            level: "debug",
            config: "test/data/config.json",
            port: yield getPort(),
            keys: [ "secret key" ]
        };

        yield main.start(args);
    });

    after(function*() {
        yield main.stop();
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

    describe("Detect", () => {
        it("should detect faces", function*() {
            let list = yield api.face.detect("node_modules/opencv/examples/files/mona.png");

            assert.equal(list.length, 1);
            assert.equal(list[0].x, 225);
            assert.equal(list[0].y, 187);
            assert.equal(list[0].w, 210);
            assert.equal(list[0].h, 210);
        });
    });
});
