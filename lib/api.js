"use strict";

const path = require("path");
const co = require("bluebird").coroutine;
const fs = require("fs-extra-promise");
const glob = require("glob-promise");
const process = require("./process");
const api = require("api.io");
const file = require("./file");

let params = {};

let cache = api.register("cache", {
    init: co(function*(config) {
        params = config;
    }),
    authenticate: function*(session, key) {
        if (params.keys.indexOf(key) === -1) {
            return false;
        }

        session.authenticated = true;
        return true;
    },
    getMetadata: function*(session, filename) {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        let exif = yield file.getExif(filename);

        let metadata = {
            raw: exif,
            name: exif.FileName,
            type: file.getType(exif.MIMEType),
            mimetype: exif.MIMEType
        };

        if (exif.SerialNumber) {
            metadata.deviceSerialNumber = exif.SerialNumber;
        } else if (exif.InternalSerialNumber) {
            metadata.deviceSerialNumber = exif.InternalSerialNumber;
        }

        if (metadata.type === "image" || metadata.type === "video") {
            metadata.width = exif.ImageWidth;
            metadata.height = exif.ImageHeight;

            if (exif.Orientation === 8) { // 270 CW (90 CCW)
                // A workaround for images which are already rotated but have not updated the exif data, should work most of the time
                if (exif.ExifImageWidth > exif.ExifImageHeight) {
                    metadata.angle = 90;
                }
            } else if (exif.Orientation === 6) { // 90 CW (270 CCW)
                // A workaround for images which are already rotated but have not updated the exif data, should work most of the time
                if (exif.ExifImageWidth > exif.ExifImageHeight) {
                    metadata.angle = 270;
                }
            } else if (exif.Orientation === 3) { // 180 CW (180 CCW)
                if (exif.ExifImageHeight > exif.ExifImageWidth) {
                    metadata.angle = 180;
                }
            }

            if (metadata.type === "image") {
                metadata.rawImage = file.isRawimage(metadata.mimetype);
            }

            metadata.where = [];
            metadata.when = [];

            if (exif.GPSDateTime && exif.GPSDateTime !== "0000:00:00 00:00:00Z" && exif.GPSDOP !== 0) { // GPSDOP == 0 is an indication of errornous data, probably just reused from earlier
                metadata.when.push({
                    type: "gps",
                    date: utils.cleanExifDate(exif.GPSDateTime)
                });

                metadata.where.push({
                    type: "gps",
                    longitude: exif.GPSLongitude,
                    latitude: exif.GPSLatitude,
                    country: exif.Country,
                    state: exif.State,
                    city: exif.City,
                    landmark: exif.Landmark === "---" ? "" : exif.Landmark,
                    area: exif.GPSAreaInformation
                });
            }

            if (exif.DateTimeOriginal) {
                metadata.when.push({
                    type: "camera",
                    date: utils.cleanExifDate(exif.DateTimeOriginal)
                });
            }
        }

        return metadata;
    },
    get: function*(session, id, filename, format) {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        if (format.angle) {
            format.angle = parseInt(format.angle, 10);

            if (format.angle === 0 || isNaN(format.angle)) {
                delete format.angle;
            } else if ([ 90, 180, 270 ].indexOf(Math.abs(format.angle)) === -1) {
                throw new Error("Valid angle values are: -270, -180, -90, 90, 180 and 270");
            }
        }

        format.filepath = path.join(params.cachePath, file.constructFilename(id, format));

        let exists = yield fs.existsAsync(format.filepath);

        if (!exists) {
            yield process.create(filename, format);
        }

        return format.filepath;
    },
    remove: function*(session, ids) {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        let files = [];

        for (let id of ids) {
            files = files.concat(yield glob(path.join(params.cachePath, id + "_*")));
        }

        for (let filename of files) {
            yield fs.removeAsync(filename);
        }

        return files.length;
    }
});

module.exports = cache;
