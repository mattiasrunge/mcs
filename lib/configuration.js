"use strict";

const path = require("path");
const fs = require("fs/promises");

module.exports = {
    init: async (args) => {
        const defaults = JSON.parse(await fs.readFile(path.join(__dirname, "..", "conf", "defaults.json")));

        Object.assign(module.exports, defaults, args);
    }
};
