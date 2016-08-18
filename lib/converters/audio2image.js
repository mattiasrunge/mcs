"use strict";

const path = require("path");
const co = require("bluebird").coroutine;
const tools = require("../tools");
const i2i = require("./image2image");

module.exports = co(function*(source, destination) {
    let filename = path.join(destination.path, "video2image.png");
    let args = [];

    destination.width = destination.width || 720;
    destination.height = destination.height || Math.floor(destination.width / 1.3333);

    args.push("-b", "efefefff");
    args.push("-c", "327DE6FF");
    args.push("-h", destination.height);
    args.push("-w", destination.width);
    args.push("-i", source.filename);
    args.push("-o", filename);

    yield tools.execute("waveform", args, { cwd: destination.path });

    source.width = destination.width;
    source.height = destination.height;
    source.filename = filename;
    source.mimetype = "image/png";

    return yield i2i(source, destination);
});
