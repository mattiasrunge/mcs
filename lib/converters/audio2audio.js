"use strict";

const path = require("path");
const co = require("bluebird").coroutine;
const promisify = require("bluebird").promisify;
const exec = promisify(require("child_process").exec);

module.exports = co(function*(source, destination) {
    let filename = path.join(destination.path, "audio2audio.webm");
    let args = [ "ffmpeg" ];

    args.push("-i", source.filename);
    args.push("-cpu-used", 0);
    args.push("-maxrate", "500k");
    args.push("-bufsize", "1000k");
    args.push("-threads", destination.cpuCount || 1);
    args.push("-codec:a", "libvorbis");
    args.push("-b:a", "128k");
    args.push("-ar", 44100);
    args.push("-f", "webm");
    args.push(filename);

    let cmd = args.join(" ");

    yield exec(cmd, { cwd: destination.path });

    return filename;
});
