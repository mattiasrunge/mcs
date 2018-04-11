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

describe("Metadata", function() {
    this.timeout(20000);

    before(async () => {
        const args = {
            level: "debug",
            config: "test/data/config.json",
            cachePath: await utils.createTmpDir(),
            port: await getPort(),
            keys: [ "secret key" ]
        };

        await fs.ensureDir(args.cachePath);

        await main.start(args);
    });

    after(async () => {
        await api.disconnect();
        await main.stop();

        await fs.remove(configuration.cachePath);
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

    describe("File", () => {
        it("should return the size of a file", async () => {
            const filename = path.resolve(__dirname, "data/image1.jpg");
            const size = await file.getSize(filename);

            assert.equal(size.width, 350);
            assert.equal(size.height, 150);
        });

        it("should return the mimetype of a file", async () => {
            const filename = path.resolve(__dirname, "data/image1.jpg");
            const mimetype = await file.getMimetype(filename);

            assert.equal(mimetype, "image/jpeg");
        });

        it("should return the exif data of a file", async () => {
            const filename = path.resolve(__dirname, "data/image1.jpg");
            const exif = await file.getExif(filename);

            assert.equal(exif.FileName, "image1.jpg");
            assert.equal(exif.FileType, "JPEG");
            assert.equal(exif.MIMEType, "image/jpeg");
            assert.equal(exif.ImageSize, "350x150");
        });

        it("should return the metadata for an image file", async () => {
            const filename = path.resolve(__dirname, "data/image1.jpg");
            const metadata = await api.metadata.get(filename);

            assert.equal(metadata.name, "image1.jpg");
            assert.equal(metadata.type, "image");
            assert.equal(metadata.mimetype, "image/jpeg");
            assert.equal(metadata.width, 350);
            assert.equal(metadata.height, 150);
            assert.equal(metadata.rawImage, false);
        });

        it("should return the metadata for a video file", async () => {
            const filename = path.resolve(__dirname, "data/video1.mp4");
            const metadata = await api.metadata.get(filename);

            assert.equal(metadata.name, "video1.mp4");
            assert.equal(metadata.type, "video");
            assert.equal(metadata.mimetype, "video/mp4");
            assert.equal(metadata.width, 560);
            assert.equal(metadata.height, 320);
        });

        it("should return the metadata for an audio file", async () => {
            const filename = path.resolve(__dirname, "data/audio1.mp3");
            const metadata = await api.metadata.get(filename);

            assert.equal(metadata.name, "audio1.mp3");
            assert.equal(metadata.type, "audio");
            assert.equal(metadata.mimetype, "audio/mpeg");
        });
    });
});
