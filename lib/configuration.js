"use strict";

const path = require("path");
const { promises: fs } = require("fs");

module.exports = {
    init: async (args) => {
        const defaults = JSON.parse(await fs.readFile(path.join(__dirname, "..", "conf", "defaults.json")));

        Object.assign(module.exports, defaults, args);

        try {
            const content = await fs.readFile("/etc/mcs.keys");
            module.exports.keys = content.toString().split("\n").filter((l) => l);
        } catch {}
    }
};
