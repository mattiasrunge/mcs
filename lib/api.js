"use strict";

const path = require("path");
const co = require("bluebird").coroutine;
const fs = require("fs-extra-promise");
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
    get: function*(session, id, filename, format) {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        format.filepath = path.join(params.cachePath, file.constructFilename(id, format.type, format.width, format.height));

        let exists = yield fs.existsAsync(format.filepath);

        if (!exists) {
            yield process.create(filename, format);
        }

        return format.filepath;
    },
    remove: function*(session, ids) {
        // TODO
        return true;
    }
});

module.exports = cache;
