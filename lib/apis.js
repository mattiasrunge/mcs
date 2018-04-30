"use strict";

const path = require("path");
const fs = require("fs/promises");
const log = require("./log")(module);

module.exports = {
    init: async (config) => {
        const apidir = path.join(__dirname, "apis");
        const filenames = await fs.readdir(apidir);

        log.info(`Loading ${filenames.length} APIs...`);

        await Promise.all(filenames
        .map((filename) => path.join(apidir, filename))
        .map(require)
        .map((api) => api.init(config)));
    }
};
