"use strict";

const path = require("path");
const glob = require("glob-promise");
const log = require("./log")(module);

module.exports = {
    init: async (config) => {
        const filenames = await glob(path.join(__dirname, "apis", "*"));

        log.info(`Loading ${filenames.length} APIs...`);

        for (const filename of filenames) {
            const api = require(filename);

            await api.init(config);
        }
    }
};
