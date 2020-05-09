"use strict";

const path = require("path");
const tools = require("../tools");

module.exports = async (source, destination) => {
    const filename = path.join(destination.path, "video2video_pass2.webm");
    const args = [];

    args.push("-i", source.filename);

    if (destination.deinterlace) {
        args.push("-vf", "yadif");
    }

    if (destination.width && destination.height) {
        args.push("-vf", `scale=${destination.width}:${destination.height}`);
    } else if (destination.width) {
        args.push("-vf", `scale=${destination.width}:-1`);
    } else if (destination.height) {
        args.push("-vf", `scale=-1:${destination.height}`);
    }

    if (destination.angle) {
        if (destination.angle === 90 || destination.angle === -270) {
            args.push("-vf", "transpose=2");
        } else if (destination.angle === 270 || destination.angle === -90) {
            args.push("-vf", "transpose=1");
        } else if (destination.angle === 180 || destination.angle === -180) {
            args.push("-vf", "transpose=1,transpose=1");
        }
    }

    args.push("-codec:v", "libvpx");
    args.push("-cpu-used", 0);
    args.push("-b:v", "500k");
    args.push("-qmin", 10);
    args.push("-qmax", 42);
    args.push("-maxrate", "500k");
    args.push("-bufsize", "1000k");
    args.push("-max_muxing_queue_size", 9999);
    args.push("-threads", destination.threads || 1);

    const argsPass1 = [];

    argsPass1.push("-an");
    argsPass1.push("-pass", 1);
    argsPass1.push("-f", "webm");
    argsPass1.push(path.join(destination.path, "video2video_pass1.webm"));

    const argsPass2 = [];

    argsPass2.push("-codec:a", "libvorbis");
    argsPass2.push("-b:a", "128k");
    argsPass2.push("-ar", 44100);
    argsPass2.push("-pass", 2);
    argsPass2.push("-f", "webm");
    argsPass2.push(filename);

    await tools.execute("ffmpeg", args.concat(argsPass1), { cwd: destination.path });
    await tools.execute("ffmpeg", args.concat(argsPass2), { cwd: destination.path });

    return filename;
};
