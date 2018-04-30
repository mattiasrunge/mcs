"use strict";

const path = require("path");
const tools = require("../tools");
const i2i = require("./image2image");

module.exports = async (source, destination) => {
    const filename = path.join(destination.path, "video2image.jpg");
    const args = [];

    args.push("-i", source.filename);

    if (destination.deinterlace) {
        args.push("-filter:v", "yadif");
    }

    if (destination.timeindex) {
        args.push("-ss", destination.timeindex);
    }

    args.push("-vframes", "1");
    args.push("-qscale", "0");
    args.push(filename);

    await tools.execute("ffmpeg", args, { cwd: destination.path });

    source.filename = filename;
    source.mimetype = "image/jpeg";

    return await i2i(source, destination);
};
