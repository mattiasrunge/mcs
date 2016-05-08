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
    get: function*(session, filename) {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        let exif = {};

        try {
            exif = yield file.getExif(filename);
        } catch (e) {
            log.error("Failed to get exif from " + filename + ", error: " + e);
            log.error("Trying secondary mimetype option");

            exif.MIMEType = yield file.getMimetype(filename);
            exif.FileName = "unknown_filename";
        }


        let data = {
            raw: exif,
            name: exif.FileName,
            type: file.getType(exif.MIMEType),
            mimetype: exif.MIMEType,
            size: yield file.getFileSize(filename),
            sha1: yield utils.checksumFile(filename, "sha1"),
            md5: yield utils.checksumFile(filename, "md5")
        };

        if (exif.SerialNumber) {
            data.deviceSerialNumber = exif.SerialNumber;
        } else if (exif.InternalSerialNumber) {
            data.deviceSerialNumber = exif.InternalSerialNumber;
        }

        if (data.type === "image" || data.type === "video") {
            data.width = exif.ImageWidth;
            data.height = exif.ImageHeight;

            if (exif.Orientation === 8) { // 270 CW (90 CCW)
                // A workaround for images which are already rotated but have not updated the exif data, should work most of the time
                if (exif.ExifImageWidth > exif.ExifImageHeight) {
                    data.angle = 90;
                }
            } else if (exif.Orientation === 6) { // 90 CW (270 CCW)
                // A workaround for images which are already rotated but have not updated the exif data, should work most of the time
                if (exif.ExifImageWidth > exif.ExifImageHeight) {
                    data.angle = 270;
                }
            } else if (exif.Orientation === 3) { // 180 CW (180 CCW)
                if (exif.ExifImageHeight > exif.ExifImageWidth) {
                    data.angle = 180;
                }
            }

            if (data.type === "image") {
                data.rawImage = file.isRawimage(data.mimetype);
            }

            data.where = [];
            data.when = [];

            if (exif.GPSDateTime && exif.GPSDateTime !== "0000:00:00 00:00:00Z" && exif.GPSDOP !== 0) { // GPSDOP == 0 is an indication of errornous data, probably just reused from earlier
                data.when.push({
                    type: "gps",
                    date: utils.cleanExifDate(exif.GPSDateTime)
                });

                data.where.push({
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
                data.when.push({
                    type: "camera",
                    date: utils.cleanExifDate(exif.DateTimeOriginal)
                });
            }
        }

        return data;
    }
});

module.exports = metadata;
