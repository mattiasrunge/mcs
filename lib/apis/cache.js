"use strict";

const path = require("path");
const co = require("bluebird").coroutine;
const fs = require("fs-extra-promise");
const glob = require("glob-promise");
const api = require("api.io");
const process = require("../process");
const file = require("../file");

let params = {};

let cache = api.register("cache", {
    init: co(function*(config) {
        params = config;
    }),
    get: function*(session, id, filename, format) {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        if (format.angle) {
            format.angle = parseInt(format.angle, 10);

            if (format.angle === 0 || isNaN(format.angle)) {
                delete format.angle;
            } else if (![ 90, 180, 270 ].includes(Math.abs(format.angle))) {
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
