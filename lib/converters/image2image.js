"use strict";

const path = require("path");
const co = require("bluebird").coroutine;
const promisifyAll = require("bluebird").promisifyAll;
const im = promisifyAll(require("imagemagick"));

module.exports = co(function*(source, destination) {
    let args = {};

    args.srcPath = source.filename;
    args.dstPath = path.join(destination.path, "image2image.jpg");
    args.strip = true;
    args.crop = false;
    args.quality = 0.91;
    args.format = "jpg";
    args.customArgs = [];

    destination.angle = parseInt(destination.angle, 10);

    if (!isNaN(destination.angle) && destination.angle) {
        args.customArgs.push("-rotate", -destination.angle, "+repage");
    }

    if (destination.mirror === true || destination.mirror === "true") {
        args.customArgs.push("-flop", "+repage");
    }

    if (destination.width) {
        args.width = destination.width;
    }

    if (destination.height) {
        args.height = destination.height;
        args.crop = true;
    }

    if (source.mimetype === "image/x-canon-cr2") {
        args.srcPath = "cr2:" + args.srcPath;
    } else if (source.mimetype === "image/x-nikon-nef") {
        args.srcPath = "nef:" + args.srcPath;
    }

    if (args.crop) {
        yield im.cropAsync(args);
    } else {
        yield im.resizeAsync(args);
    }

    return args.dstPath;
});
