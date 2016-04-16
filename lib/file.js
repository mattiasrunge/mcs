"use strict";

const co = require("bluebird").coroutine;
const promisify = require("bluebird").promisify;
const exec = promisify(require("child_process").exec);
const mimetypes = require("./mimetypes.json");

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
    isRawimage: (mimeType) => mimetypes.rawimage.indexOf(mimeType) !== -1,
    isImage: (mimeType) => mimetypes.image.indexOf(mimeType) !== -1,
    isVideo: (mimeType) => mimetypes.video.indexOf(mimeType) !== -1,
    isAudio: (mimeType) => mimetypes.audio.indexOf(mimeType) !== -1,
    getMimetype: co(function*(filename) {
        let cmd = "exiftool -j -n -MIMEType " + filename;
        let stdout = yield exec(cmd);
        let data = JSON.parse(stdout)[0];

        return data.MIMEType;
    }),
    getSize: co(function*(filename) {
        let cmd = "exiftool -j -n -ImageWidth -ImageHeight -MIMEType " + filename;
        let stdout = yield exec(cmd);
        let data = JSON.parse(stdout)[0];

        return { width: data.ImageWidth, height: data.ImageHeight, mimetype: data.MIMEType };
    }),
    getExif: co(function*(filename) {
        let exclude = [
            "DataDump",
            "ThumbnailImage",
            "Directory",
            "FilePermissions",
            "TextJunk",
            "ColorBalanceUnknown",
            "Warning"
        ];
        let cmd = "exiftool -j -n " + exclude.map((e) => "-x " + e).join(" ") + " " + filename;
        let stdout = yield exec(cmd);
        let data = JSON.parse(stdout)[0];

        if (data.DateTimeOriginal === "    :  :     :  :  ") {
            delete data.DateTimeOriginal;
        }

        return data;
    })
};
