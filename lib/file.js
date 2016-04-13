"use strict";

const co = require("bluebird").coroutine;
const promisify = require("bluebird").promisify;
const exec = promisify(require("child_process").exec);
const mimetypes = require("./mimetypes.json");

module.exports = {
    constructFilename: (id, type, width, height) => {
        let square = width === height ? 1 : 0;
        let extension = type === "image" ? "jpg" : "webm";
        height = height || 0;

        return id + "_" + width + "x" + height + "_" + square + "." + extension;
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
