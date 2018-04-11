"use strict";

const path = require("path");
const fs = require("fs-extra");
const glob = require("glob-promise");
const api = require("api.io");
const process = require("../process");
const file = require("../file");

const cache = api.register("cache", {
    init: async (/* config */) => {
    },
    getAll: api.export(async (session, id, filename, type, cachePath) => {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        const extension = file.getExtensionByType(type);

        return await glob(path.join(cachePath, `${id}_*.${extension}`));
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
        const exists = await fs.pathExists(format.filepath);

        if (!exists) {
            if (!(await fs.pathExists(filename))) {
                const error = new Error(`Could not find ${filename}`);
                error.stack = false;
                throw error;
            }

            await process.create(filename, format);
        }

        return format.filepath;
    }),
    remove: api.export(async (session, ids, cachePath) => {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        let files = [];

        for (const id of ids) {
            files = files.concat(await glob(path.join(cachePath, `${id}_*`)));
        }

        for (const filename of files) {
            await fs.remove(filename);
        }

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
