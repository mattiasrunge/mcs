"use strict";

const path = require("path");
const co = require("bluebird").coroutine;
const promisify = require("bluebird").promisify;
const exec = promisify(require("child_process").exec);
const i2i = require("./image2image");

module.exports = co(function*(source, destination) {
    let filename = path.join(destination.path, "video2image.jpg");
    let args = [ "avconv" ];

    args.push("-i", source.filename);

    if (destination.deinterlace) {
        args.push("-filter:v", "yadif");
    }

    if (destination.timeindex) {
        args.push("-ss", destination.timeindex);
    }

    args.push("-t", "00:00:01");
    args.push("-r", "1");
    args.push("-y");
    args.push("-an");
    args.push("-qscale", "0");
    args.push(filename);

    let cmd1 = args.join(" ");

    yield exec(cmd1, { cwd: destination.path });

    source.filename = filename;
    source.mimetype = "image/jpeg";

    return yield i2i(source, destination);
});
