"use strict";

const path = require("path");
const tools = require("../tools");
const d2d = require("./document2document");
const i2i = require("./image2image");

module.exports = async (source, destination) => {
    source.filename = await d2d(source, destination);
    source.mimetype = "application/pdf";

    let filename = path.join(destination.path, "document2image1.jpg");
    let args = [];

    args.push(`${source.filename}[0]`);
    // TODO: Use destination image width and height if they are defined
    args.push("-thumbnail", "x590");
    args.push("-background", "white");
    args.push("-alpha", "remove");
    args.push("-quality", 91);

    args.push(filename);

    await tools.execute("convert", args, { cwd: destination.path });

    source.filename = filename;
    source.mimetype = "image/jpeg";

    filename = path.join(destination.path, "document2image2.jpg");
    args = [];

    args.push(source.filename);
    args.push("-gravity", "center");
    args.push("-background", "#EFEFEF");
    args.push("-extent", "600x600");
    args.push("-quality", 91);

    args.push(filename);

    await tools.execute("convert", args, { cwd: destination.path });

    source.filename = filename;
    source.mimetype = "image/jpeg";


    return await i2i(source, destination);
};
