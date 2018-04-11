"use strict";

/* global describe before after it */

const getPort = require("get-port");
const assert = require("assert");
const api = require("api.io/api.io-client");
const main = require("../lib/main");
const configuration = require("../lib/configuration");

describe("Face", function() {
    this.timeout(20000);

    before(async () => {
        const args = {
            level: "debug",
            config: "test/data/config.json",
            port: await getPort(),
            keys: [ "secret key" ]
        };

        await main.start(args);
    });

    after(async () => {
        await api.disconnect();
        await main.stop();
    });

    describe("Setup", () => {
        it("should connect", async () => {
            await api.connect({
                hostname: "localhost",
                port: configuration.port
            });
        });

        it("should authenticate", async () => {
            const result = await api.auth.identify("secret key");

            assert(result);
        });
    });

    describe("Detect", () => {
        it("should detect faces", async () => {
            const list = await api.face.detect("node_modules/opencv/examples/files/mona.png");

            assert.equal(list.length, 1);
            assert(list[0].confidence > 1);
            assert.equal(list[0].x, 227);
            assert.equal(list[0].y, 191);
            assert.equal(list[0].w, 206);
            assert.equal(list[0].h, 204);
        });
    });
});
