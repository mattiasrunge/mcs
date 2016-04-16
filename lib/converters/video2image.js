"use strict";

const path = require("path");
const co = require("bluebird").coroutine;
const tools = require("../tools");
const i2i = require("./image2image");

module.exports = co(function*(source, destination) {
    let filename = path.join(destination.path, "video2image.jpg");
    let args = [];

    args.push("-i", source.filename);

    if (destination.deinterlace) {
        args.push("-filter:v", "yadif");
    }

    if (destination.timeindex) {
        args.push("-ss", destination.timeindex);
    }

    args.push("-vframes", "1");
    args.push("-qscale", "0");
    args.push(filename);

    yield tools.execute("avconv", args, { cwd: destination.path });

    source.filename = filename;
    source.mimetype = "image/jpeg";

    return yield i2i(source, destination);
});
