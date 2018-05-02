"use strict";

const path = require("path");
const tools = require("../tools");
const i2i = require("./image2image");

module.exports = async (source, destination) => {
    const filename = path.join(destination.path, "video2image.png");
    const args = [];

    destination.width = destination.width || 720;
    destination.height = destination.height || Math.floor(destination.width / 1.3333);

    // Inspired by https://stackoverflow.com/questions/32254818/generating-a-waveform-using-ffmpeg
    args.push("-i", source.filename);
    args.push("-filter_complex", `[0:a]aformat=channel_layouts=mono,compand,showwavespic=s=${destination.width}x${destination.height}:colors=#2185D0[fg];color=s=${destination.width}x${destination.height}:color=#E0E0E0,drawgrid=width=iw/10:height=ih/5:color=#CACACA[bg];[bg][fg]overlay=format=rgb,drawbox=x=(iw-w)/2:y=(ih-h)/2:w=iw:h=1:color=#2185D0`);
    args.push("-frames:v", 1);
    args.push(filename);

    await tools.execute("ffmpeg", args, { cwd: destination.path });

    source.width = destination.width;
    source.height = destination.height;
    source.filename = filename;
    source.mimetype = "image/png";

    return await i2i(source, destination);
};
