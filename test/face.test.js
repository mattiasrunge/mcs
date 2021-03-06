"use strict";

/* global describe beforeAll afterAll it jest */

const path = require("path");
const getPort = require("get-port");
const assert = require("assert");
const api = require("api.io").getClient();
const main = require("../lib/main");
const configuration = require("../lib/configuration");

describe("Face", () => {
    beforeAll(async () => {
        const args = {
            level: "debug",
            config: "test/data/config.json",
            port: await getPort()
        };

        await main.start(args);
    });

    afterAll(async () => {
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
            jest.setTimeout(60000);

            const filename = path.resolve(__dirname, "data/mona.jpg");
            const list = await api.face.detect(filename);

            assert.equal(list.length, 1);
            assert.equal(list[0].x, 0.456);
            assert.equal(list[0].y, 0.24802);
            assert.equal(list[0].w, 0.4686);
            assert.equal(list[0].h, 0.30774);
        });
    });
});
