"use strict";

const co = require("bluebird").coroutine;
const mimetypes = require("./mimetypes.json");
const tools = require("./tools");

module.exports = {
    constructFilename: (id, format) => {
        let square = format.width === format.height ? 1 : 0;
        let extension = format.type === "image" ? "jpg" : "webm";
        let height = format.height || 0;
        let width = format.width || 0;
        let angle = format.angle || 0;
        let mirror = format.mirror ? 1 : 0;

        return id +
               "_w" + width +
               "_h" + height +
               "_s" + square +
               "_a" + angle +
               "_m" + mirror +
               "." + extension;
    },
    isRawimage: (mimetype) => mimetypes.rawimage.indexOf(mimetype) !== -1,
    isImage: (mimetype) => mimetypes.image.indexOf(mimetype) !== -1,
    isVideo: (mimetype) => mimetypes.video.indexOf(mimetype) !== -1,
    isAudio: (mimetype) => mimetypes.audio.indexOf(mimetype) !== -1,
    getType: (mimetype) => {
        if (module.exports.isImage(mimetype) || module.exports.isRawimage(mimetype)) {
            return "image";
        } else if (module.exports.isVideo(mimetype)) {
            return "video";
        } else if (module.exports.isAudio(mimetype)) {
            return "audio";
        }

        return "other";
    },
    getMimetype: co(function*(filename) {
        let args = [];
        args.push("-j");
        args.push("-n");
        args.push("-MIMEType");
        args.push(filename);

        let data = yield tools.execute("exiftool", args, { json: true });

        return data[0].MIMEType;
    }),
    getSize: co(function*(filename) {
        let args = [];
        args.push("-j");
        args.push("-n");
        args.push("-ImageWidth");
        args.push("-ImageHeight");
        args.push(filename);

        let data = yield tools.execute("exiftool", args, { json: true });

        return { width: data[0].ImageWidth, height: data[0].ImageHeight };
    }),
    getExif: co(function*(filename) {
        let args = [];
        args.push("-j");
        args.push("-n");
        args.push("-x", "DataDump");
        args.push("-x", "ThumbnailImage");
        args.push("-x", "Directory");
        args.push("-x", "FilePermissions");
        args.push("-x", "TextJunk");
        args.push("-x", "ColorBalanceUnknown");
        args.push("-x", "Warning");
        args.push(filename);

        let data = yield tools.execute("exiftool", args, { json: true });

        if (data[0].DateTimeOriginal === "    :  :     :  :  ") {
            delete data[0].DateTimeOriginal;
        }

        return data[0];
    })
};
