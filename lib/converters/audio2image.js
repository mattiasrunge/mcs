"use strict";

const path = require("path");
const co = require("bluebird").coroutine;
const promisify = require("bluebird").promisify;
const tools = require("../tools");
const waveform = promisify(require("waveform"));
const i2i = require("./image2image");

module.exports = co(function*(source, destination) {
    let filename = path.join(destination.path, "video2image.png");

    destination.width = destination.width || 720;
    destination.height = destination.height || Math.floor(destination.width/1.3333);

    yield waveform(source.filename, {
        png: filename,
        "png-color-bg": "ffffffff",
        "png-color-center": "327DE6FF",
        "png-color-outer": "000000ff",
        "png-width": destination.width,
        "png-height": destination.height
    });

    source.width = destination.width;
    source.height = destination.height;
    source.filename = filename;
    source.mimetype = "image/png";

    return yield i2i(source, destination);
});
