"use strict";

const path = require("path");
const tools = require("../tools");
const i2i = require("./image2image");

module.exports = async (source, destination) => {
    const filename = path.join(destination.path, "video2image.png");
    const args = [];

    destination.width = destination.width || 720;
    destination.height = destination.height || Math.floor(destination.width / 1.3333);

    args.push("-b", "efefefff");
    args.push("-c", "327DE6FF");
    args.push("-h", destination.height);
    args.push("-w", destination.width);
    args.push("-i", source.filename);
    args.push("-o", filename);

    await tools.execute("waveform", args, { cwd: destination.path });

    source.width = destination.width;
    source.height = destination.height;
    source.filename = filename;
    source.mimetype = "image/png";

    return await i2i(source, destination);
};
