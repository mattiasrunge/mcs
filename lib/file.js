"use strict";

const { promises: fs } = require("fs");
const mimetypes = require("./mimetypes.json");
const tools = require("./tools");
const checksum = require("checksum");

module.exports = {
    getExtensionByType: (type) => {
        if (type === "image") {
            return "jpg";
        } else if (type === "video" || type === "audio") {
            return "webm";
        } else if (type === "document") {
            return "pdf";
        } else if (type === "*") {
            return;
        }

        throw new Error(`Unknown format type, ${type}`);
    },
    getTypeByExtension: (extension) => {
        if (extension === "jpg") {
            return "image";
        } else if (extension === "webm") {
            return "video";
        } else if (extension === "pdf") {
            return "document";
        }

        throw new Error(`Unknown extension, ${extension}`);
    },
    deconstructFilename: (id, filename) => {
        const [
            ,
            ,
            width,
            height,
            ,
            angle,
            mirror,
            extension
        ] = filename.match(/^(.*?)_w(.*?)_h(.*?)_s(.*?)_a(.*?)_m(.*?)\.(.*?)$/);

        return {
            type: module.exports.getTypeByExtension(extension),
            height: parseInt(height, 10),
            width: parseInt(width, 10),
            angle: parseInt(angle, 10),
            mirror: parseInt(mirror, 10) === 1
        };
    },
    constructFilename: (id, format) => {
        const square = format.width === format.height ? 1 : 0;
        const extension = module.exports.getExtensionByType(format.type);
        const height = format.height || 0;
        const width = format.width || 0;
        const angle = format.angle || 0;
        const mirror = format.mirror ? 1 : 0;

        return [
            id,
            "_w", width,
            "_h", height,
            "_s", square,
            "_a", angle,
            "_m", mirror,
            ".", extension
        ].join("");
    },
    isRawimage: (mimetype) => mimetypes.rawimage.includes(mimetype),
    isImage: (mimetype) => mimetypes.image.includes(mimetype),
    isVideo: (mimetype) => mimetypes.video.includes(mimetype),
    isAudio: (mimetype) => mimetypes.audio.includes(mimetype),
    isPdf: (mimetype) => mimetypes.pdf.includes(mimetype),
    isDocument: (mimetype) => mimetypes.document.includes(mimetype),
    isMsDocument: (mimetype) => mimetypes.msdocument.includes(mimetype),
    getType: (mimetype) => {
        if (module.exports.isImage(mimetype)) {
            return "image";
        } else if (module.exports.isVideo(mimetype)) {
            return "video";
        } else if (module.exports.isAudio(mimetype)) {
            return "audio";
        } else if (module.exports.isDocument(mimetype)) {
            return "document";
        }

        return "other";
    },
    getMimetype: async (filename) => {
        let args = [];
        args.push("-j");
        args.push("-n");
        args.push("-MIMEType");
        args.push(filename);

        let mimetype = false;

        try {
            const data = await tools.execute("exiftool", args, { json: true });
            mimetype = data[0].MIMEType;
        } catch (e) {
            args = [];
            args.push("-b");
            args.push("-L");
            args.push("--mime-type");
            args.push(filename);

            mimetype = await tools.execute("file", args);
            mimetype = mimetype.replace(/\n/g, "");
        }

        return mimetype;
    },
    getSize: async (filename) => {
        const args = [];
        args.push("-j");
        args.push("-n");
        args.push("-Rotation");
        args.push("-ImageWidth");
        args.push("-ImageHeight");
        args.push(filename);

        const data = await tools.execute("exiftool", args, { json: true });

        // TODO: Can we remove this, it causes issues and should be a display issue that looks at the set angle not the exif data
        // if (data[0].Rotation === 90 || data[0].Rotation === 270) {
        //     return { height: data[0].ImageWidth, width: data[0].ImageHeight };
        // }

        return { width: data[0].ImageWidth, height: data[0].ImageHeight };
    },
    getExif: async (filename) => {
        const args = [];
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

        const data = await tools.execute("exiftool", args, { json: true });

        if (data[0].DateTimeOriginal === "    :  :     :  :  ") {
            delete data[0].DateTimeOriginal;
        }

        return data[0];
    },
    getFileSize: async (filename) => {
        const { size } = await fs.stat(filename);

        return size;
    },
    detectFaces: async (filename) => {
        return await tools.execute("detectfaces", [ filename ], { json: true });
    },
    checksumFile: (filename, algorithm) => {
        return new Promise((resolve, reject) => {
            const options = {};

            if (algorithm) {
                options.algorithm = algorithm;
            }

            checksum.file(filename, options, (error, sum) => {
                if (error) {
                    return reject(error);
                }

                resolve(sum);
            });
        });
    }
};
