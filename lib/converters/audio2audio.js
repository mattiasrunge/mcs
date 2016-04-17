"use strict";

const path = require("path");
const co = require("bluebird").coroutine;
const tools = require("../tools");

module.exports = co(function*(source, destination) {
    let filename = path.join(destination.path, "audio2audio.webm");
    let args = [];

    args.push("-i", source.filename);
    args.push("-cpu-used", 0);
    args.push("-maxrate", "500k");
    args.push("-bufsize", "1000k");
    args.push("-threads", destination.threads || 1);
    args.push("-codec:a", "libvorbis");
    args.push("-b:a", "128k");
    args.push("-ar", 44100);
    args.push("-f", "webm");
    args.push(filename);

    yield tools.execute("avconv", args, { cwd: destination.path });

    return filename;
});
