"use strict";

const path = require("path");
const co = require("bluebird").coroutine;
const tools = require("../tools");
const d2d = require("./document2document");
const i2i = require("./image2image");

module.exports = co(function*(source, destination) {
    source.filename = yield d2d(source, destination);
    source.mimetype = "application/pdf";

    let filename = path.join(destination.path, "document2image1.jpg");
    let args = [];

    args.push(source.filename + "[0]");

    args.push("-thumbnail", "x600");
    args.push("-background", "white");
    args.push("-alpha", "remove");
    args.push("-quality", 91);


    args.push(filename);

    yield tools.execute("convert", args, { cwd: destination.path });

    source.filename = filename;
    source.mimetype = "image/jpeg";

    filename = path.join(destination.path, "document2image2.jpg");
    args = [];

    args.push(source.filename);

    args.push("-gravity", "center");
    args.push("-background", "#EFEFEF");
    args.push("-extent", "600x600");
    args.push("-bordercolor", "#EFEFEF");
    args.push("-border", "10x10");
    args.push("-quality", 91);

    args.push(filename);

    yield tools.execute("convert", args, { cwd: destination.path });

    source.filename = filename;
    source.mimetype = "image/jpeg";


    return yield i2i(source, destination);
});
