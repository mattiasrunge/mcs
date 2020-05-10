"use strict";

const path = require("path");
const { promises: fs } = require("fs");
const tools = require("../tools");
const file = require("../file");

module.exports = async (source, destination) => {
    const filename = path.join(destination.path, "image2image.jpg");
    const args = [];

    if (file.isRawimage(source.mimetype)) {
        const newSource = path.join(destination.path, path.basename(source.filename));
        const ext = path.extname(source.filename);

        await fs.symlink(source.filename, newSource);
        await tools.execute("dcraw", [ newSource ], { cwd: destination.path });

        args.push(path.join(destination.path, `${path.basename(source.filename, ext)}.tiff`));
    } else if (source.mimetype === "image/gif") {
        args.push(`${source.filename}[0]`);
    } else {
        args.push(source.filename);
    }

    args.push("-set", "optionsion:filter:blur", 0.8);// Needed?
    args.push("-filter", "Lagrange");
    args.push("-strip");

    if (destination.angle) {
        args.push("-rotate", -destination.angle, "+repage");

        if (Math.abs(destination.angle) === 90 || Math.abs(destination.angle) === 270) {
            const width = source.width;
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
        const sourceRatio = source.width / source.height;
        const targetRatio = targetWidth / targetHeight;

        args.push("-resize", sourceRatio < targetRatio ? `${targetWidth}x` : `x${targetHeight}`);
        args.push("-gravity", "Center");
        args.push("-crop", `${targetWidth}x${targetHeight}+0+0`);
        args.push("+repage");
    } else {
        args.push("-resize");
        args.push(`${targetWidth || ""}x${targetHeight || ""}`);
    }

    args.push("-quality", 91);

    if (destination.mirror === true || destination.mirror === "true") {
        args.push("-flop", "+repage");
    }

    args.push(filename);

    await tools.execute("convert", args, { cwd: destination.path });

    return filename;
};
