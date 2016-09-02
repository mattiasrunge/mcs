"use strict";

const path = require("path");
const co = require("bluebird").coroutine;
const tools = require("../tools");

module.exports = co(function*(source, destination) {
    let filename = path.join(destination.path, "pdfimage.jpg");
    let args = [];

    args.push(source.filename + "[0]");

    args.push("-strip");
    args.push("-background", "white");
    args.push("-alpha", "remove");

    if (destination.angle) {
        args.push("-rotate", -destination.angle, "+repage");

        if (Math.abs(destination.angle) === 90 || Math.abs(destination.angle) === 270) {
            let width = source.width;
            source.width = source.height;
            source.height = width;
        }
    }

    let targetWidth = destination.width || 0;
    let targetHeight = destination.height || 0;

    if (!destination.width && !destination.height) {
        targetWidth = source.width;
        targetHeight = source.height;
    }

    if (destination.height) {
        let sourceRatio = source.width / source.height;
        let targetRatio = targetWidth / targetHeight;

        args.push("-resize", sourceRatio < targetRatio ? targetWidth + "x" : "x" + targetHeight);
        args.push("-gravity", "Center");
        args.push("-crop", targetWidth + "x" + targetHeight + "+0+0");
        args.push("+repage");
    } else {
        args.push("-resize");
        args.push((targetWidth || "") + "x" + (targetHeight || ""));
    }

    args.push("-quality", 91);

    if (destination.mirror === true || destination.mirror === "true") {
        args.push("-flop", "+repage");
    }

    args.push(filename);

    yield tools.execute("convert", args, { cwd: destination.path });

    return filename;
});
