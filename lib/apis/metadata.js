"use strict";

const co = require("bluebird").coroutine;
const api = require("api.io");
const file = require("../file");
const utils = require("../utils");
const log = require("../log")(module);

let params = {};

let metadata = api.register("metadata", {
    init: co(function*(config) {
        params = config;
    }),
    get: function*(session, filename, options) {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        let exif = {};

        try {
            exif = yield file.getExif(filename);
        } catch (e) {
            log.warn("Failed to get exif from " + filename + ", error: " + e);
            log.warn("Trying secondary mimetype option");

            exif.MIMEType = yield file.getMimetype(filename);
            exif.FileName = "unknown_filename";
            log.warn("Secondary mimetype option for " + filename + " resulted in " + exif.MIMEType);
        }


        let data = {
            raw: exif,
            name: exif.FileName,
            type: file.getType(exif.MIMEType),
            mimetype: exif.MIMEType,
            size: yield file.getFileSize(filename),
            deviceinfo: {
                model: exif.Model,
                make: exif.Make
            },
            fileinfo: {
                number: exif.FileNumber,
                width: exif.ImageWidth,
                height: exif.ImageHeight,
                fps: exif.FrameRate,
                iso: exif.ISO,
                exposure: exif.ExposureTime,
                frames: exif.FrameCount,
                duration: exif.Duration
            }
        };

        if (!options.noChecksums) {
            data.sha1 = yield utils.checksumFile(filename, "sha1");
            data.md5 = yield utils.checksumFile(filename, "md5");
        }

        if (exif.SerialNumber) {
            data.deviceSerialNumber = exif.SerialNumber.toString().replace(/\u0000/g, "");
        } else if (exif.InternalSerialNumber) {
            data.deviceSerialNumber = exif.InternalSerialNumber.toString().replace(/\u0000/g, "");
        }

        if (data.type === "image" || data.type === "video") {
            data.width = exif.ImageWidth;
            data.height = exif.ImageHeight;
            data.where = {};
            data.when = {};

            if (exif.Rotation === 90 || exif.Rotation === 270) {
                data.fileinfo.width = data.width = exif.ImageHeight;
                data.fileinfo.height = data.height = exif.ImageWidth;
            }

            if (data.deviceinfo.make === "Apple" && data.type === "video") {
                if (exif.GPSLongitude && exif.GPSLatitude) {
                    data.where.gps = {
                        longitude: exif.GPSLongitude,
                        latitude: exif.GPSLatitude,
                        altitude: exif.GPSAltitude
                    };
                }

                if (exif.CreationDate) {
                    data.when.device = utils.parseExifDate(exif.CreationDate);
                    data.when.device.deviceType = "unknown";
                    data.when.device.deviceAutoDst = false;
                    data.when.device.deviceUtcOffset = 0;
                }
            } else {
                // A workaround for images which are already rotated but have not updated the exif data
                // images are landscape mode by default, if the image is not we ignore the orientation
                if (exif.ImageHeight < exif.ImageWidth) {
                    if (exif.Orientation === 8) { // 270 CW (90 CCW)
                        data.angle = 90;
                    } else if (exif.Orientation === 6) { // 90 CW (270 CCW)
                        data.angle = 270;
                    } else if (exif.Orientation === 3) { // 180 CW (180 CCW)
                        data.angle = 180;
                    }
                }

                if (data.type === "image") {
                    data.rawImage = file.isRawimage(data.mimetype);
                }

                if (exif.GPSDateTime && exif.GPSDateTime !== "0000:00:00 00:00:00Z" && exif.GPSDOP !== 0) { // GPSDOP == 0 is an indication of errornous data, probably just reused from earlier
                    data.when.gps = utils.parseExifDate(exif.GPSDateTime);

                    data.where.gps = {
                        longitude: exif.GPSLongitude,
                        latitude: exif.GPSLatitude,
                        altitude: exif.GPSAltitude,
                        country: exif.Country,
                        state: exif.State,
                        city: exif.City,
                        landmark: exif.Landmark === "---" ? "" : exif.Landmark,
                        area: exif.GPSAreaInformation
                    };
                }

                if (exif.DateTimeOriginal) {
                    data.when.device = utils.parseExifDate(exif.DateTimeOriginal);
                    data.when.device.deviceType = "unknown";
                    data.when.device.deviceAutoDst = false;
                    data.when.device.deviceUtcOffset = 0;
                }
            }
        }

        return data;
    }
});

module.exports = metadata;
