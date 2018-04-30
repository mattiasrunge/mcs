"use strict";

const path = require("path");
const tools = require("../tools");
const i2i = require("./image2image");

module.exports = async (source, destination) => {
    const filename = path.join(destination.path, "video2image.png");
    const args = [];

    destination.width = destination.width || 720;
    destination.height = destination.height || Math.floor(destination.width / 1.3333);

    // TODO: Add colors when using newer ffmpeg
    args.push("-i", source.filename);
    args.push("-filter_complex", `compand,showwavespic=s=${destination.width}x${destination.height}`);
    args.push("-frames:v", 1);
    args.push(filename);

    await tools.execute("ffmpeg", args, { cwd: destination.path });

    source.width = destination.width;
    source.height = destination.height;
    source.filename = filename;
    source.mimetype = "image/png";

    return await i2i(source, destination);
};
