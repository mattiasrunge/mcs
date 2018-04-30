"use strict";

const path = require("path");
const fs = require("fs/promises");
const { constants } = require("fs");
const api = require("api.io");
const process = require("../process");
const file = require("../file");

const cache = api.register("cache", {
    init: async (/* config */) => {
    },
    getAll: api.export(async (session, id, filename, type, cachePath, options = {}) => {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        const extension = file.getExtensionByType(type);

        const names = await fs.readdir(cachePath);
        const files = names
        .filter((name) => name.split("_", 1)[0] === id.toString())
        .filter((name) => name.endsWith(`.${extension}`))
        .map((name) => path.join(cachePath, name));

        if (options.format) {
            return files.map((item) => file.deconstructFilename(id, item));
        }

        return files;
    }),
    get: api.export(async (session, id, filename, format, cachePath) => {
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

        format.filepath = path.join(cachePath, file.constructFilename(id, format));

        try {
            await fs.access(format.filepath);

            return format.filepath;
        } catch {}

        await fs.access(filename, constants.R_OK);
        await process.create(filename, format);

        return format.filepath;
    }),
    remove: api.export(async (session, ids, cachePath) => {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        ids = ids.map((id) => id.toString());

        const names = await fs.readdir(cachePath);
        const files = names
        .filter((name) => ids.includes(name.split("_", 1)[0]))
        .map((name) => path.join(cachePath, name));

        await Promise.all(files.map(fs.unlink));

        return files.length;
    }),
    status: api.export(async (session) => {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        return process.status();
    })
});

module.exports = cache;
