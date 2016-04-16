"use strict";

const path = require("path");
const co = require("bluebird").coroutine;
const tools = require("../tools");

module.exports = co(function*(source, destination) {
    let filename = path.join(destination.path, "image2image.jpg");
    let args = [];

    if (source.mimetype === "image/x-canon-cr2") {
        args.push("cr2:" + source.filename);
    } else if (source.mimetype === "image/x-nikon-nef") {
        args.push("nef:" + source.filename);
    } else {
        args.push(source.filename);
    }

    args.push("-set", "optionsion:filter:blur", 0.8);// Needed?
    args.push("-filter", "Lagrange");
    args.push("-strip");

    destination.angle = parseInt(destination.angle, 10);
    if (!isNaN(destination.angle) && destination.angle) {
        let abs = Math.abs(destination.angle);

        if (abs !== 90 && abs !== 180) {
            throw new Error("Only -180, -90, 90 and 180 degrees are valid angles");
        }

        args.push("-rotate", -destination.angle, "+repage");

        if (abs === 90) {
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
