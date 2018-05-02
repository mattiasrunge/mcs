"use strict";

/* global describe before after it */

const path = require("path");
const getPort = require("get-port");
const fs = require("fs-extra");
const assert = require("assert");
const api = require("api.io/api.io-client");
const main = require("../lib/main");
const configuration = require("../lib/configuration");
const file = require("../lib/file");
const utils = require("../lib/utils");

let cachePath;
let idCounter = 0;

describe("Cache", function() {
    this.timeout(20000);

    before(async () => {
        const args = {
            level: "debug",
            config: "test/data/config.json",
            port: await getPort()
        };

        await main.start(args);

        cachePath = await utils.createTmpDir();
        await fs.ensureDir(cachePath);
    });

    after(async () => {
        await api.disconnect();
        await main.stop();

        await fs.remove(cachePath);
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

    describe("Image2Image", () => {
        it("should resize a file with only width set", async () => {
            const filename = path.resolve(__dirname, "data/image1.jpg");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "image",
                width: 20
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 9);
        });

        it("should resize a file with width and height set", async () => {
            const filename = path.resolve(__dirname, "data/image1.jpg");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "image",
                width: 20,
                height: 30
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 30);
        });

        it("should resize a file with width and height equal", async () => {
            const filename = path.resolve(__dirname, "data/image1.jpg");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "image",
                width: 20,
                height: 20
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 20);
        });

        it("should rotate a file 90 degrees", async () => {
            const filename = path.resolve(__dirname, "data/image1.jpg");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "image",
                angle: 90
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 150);
            assert.equal(size.height, 350);
        });

        it("should mirror a file", async () => {
            const filename = path.resolve(__dirname, "data/image1.jpg");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "image",
                mirror: true
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 350);
            assert.equal(size.height, 150);
        });

        it("should rotate and resize a file", async () => {
            const filename = path.resolve(__dirname, "data/image1.jpg");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "image",
                angle: 90,
                width: 75,
                height: 175
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 75);
            assert.equal(size.height, 175);
        });

        it("should get all cached versions", async () => {
            const filename = path.resolve(__dirname, "data/image1.jpg");
            const id = idCounter - 1;

            const result = await api.cache.getAll(id, filename, "image", cachePath);

            assert.equal(result.length, 1);
        });
    });

    describe("Video2Image", () => {
        it("should resize a file with only width set", async () => {
            const filename = path.resolve(__dirname, "data/video1.mp4");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "image",
                width: 20
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 11);
        });

        it("should resize a file with width and height set", async () => {
            const filename = path.resolve(__dirname, "data/video1.mp4");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "image",
                width: 20,
                height: 30
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 30);
        });

        it("should resize a file with width and height equal", async () => {
            const filename = path.resolve(__dirname, "data/video1.mp4");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "image",
                width: 20,
                height: 20
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 20);
        });
    });

    describe("Video2Video", () => {
        it("should convert a file", async () => {
            const filename = path.resolve(__dirname, "data/video1.mp4");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "video"
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);
        });

        it("should resize a file with only width set", async () => {
            const filename = path.resolve(__dirname, "data/video1.mp4");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "video",
                width: 20
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 11);
        });

        it("should resize a file with width and height set", async () => {
            const filename = path.resolve(__dirname, "data/video1.mp4");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "video",
                width: 20,
                height: 30
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 30);
        });

        it("should resize a file with width and height equal", async () => {
            const filename = path.resolve(__dirname, "data/video1.mp4");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "video",
                width: 20,
                height: 20
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 20);
        });

        it("should rotate a video", async () => {
            const filename = path.resolve(__dirname, "data/video1.mp4");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "video",
                angle: 90
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 320);
            assert.equal(size.height, 560);
        });
    });

    describe("Audio2Audio", () => {
        it("should convert a file", async () => {
            const filename = path.resolve(__dirname, "data/audio1.mp3");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "audio"
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);
        });
    });

    describe("Audio2Image", () => {
        it("should resize a file with only width set", async () => {
            const filename = path.resolve(__dirname, "data/audio1.mp3");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "image",
                width: 20
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 15);
        });

        it("should resize a file with width and height set", async () => {
            const filename = path.resolve(__dirname, "data/audio1.mp3");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "image",
                width: 20,
                height: 30
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 30);
        });

        it("should resize a file with width and height equal", async () => {
            const filename = path.resolve(__dirname, "data/audio1.mp3");
            const id = idCounter++;

            const result = await api.cache.get(id, filename, {
                type: "image",
                width: 20,
                height: 20
            }, cachePath);

            const exists = await fs.pathExists(result);
            assert(exists);

            const size = await file.getSize(result);
            assert.equal(size.width, 20);
            assert.equal(size.height, 20);
        });
    });

    describe("Remove", () => {
        it("should create two files and then remove them", async () => {
            const filename = path.resolve(__dirname, "data/image1.jpg");
            const id = idCounter++;

            const result1 = await api.cache.get(id, filename, {
                type: "image",
                width: 20
            }, cachePath);

            const result2 = await api.cache.get(id, filename, {
                type: "image",
                width: 30
            }, cachePath);

            assert(await fs.pathExists(result1));
            assert(await fs.pathExists(result2));

            const result = await api.cache.remove([ id ], cachePath);

            assert.equal(result, 2);

            assert(!(await fs.pathExists(result1)));
            assert(!(await fs.pathExists(result2)));
        });
    });
});
