"use strict";

/* global describe before after it */

const path = require("path");
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
            port: await getPort()
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
            const result = await api.auth.identify(configuration.keys[0]);

            assert(result);
        });
    });

    describe("Detect", () => {
        it("should detect faces", async () => {
            const filename = path.resolve(__dirname, "data/mona.jpg");
            const list = await api.face.detect(filename);

            assert.equal(list.length, 1);
            assert.equal(list[0].x, 0.448);
            assert.equal(list[0].y, 0.27778);
            assert.equal(list[0].w, 0.4092);
            assert.equal(list[0].h, 0.27063);
        });
    });
});
