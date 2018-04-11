"use strict";

const configuration = require("./configuration");
const logger = require("./log");
const log = logger(module);
const apis = require("./apis");
const server = require("./server");
const process = require("./process");
const tools = require("./tools");

module.exports = {
    start: async (args, version) => {
        await logger.init(args.level);

        log.info(`Media Cache Server v${version} starting...`);

        await configuration.init(args);
        await tools.init(configuration);
        await process.init(configuration);
        await apis.init(configuration);
        await server.init(configuration, version);

        log.info("Initialization complete.");
    },
    stop: async () => {
        log.info("Received shutdown signal, stoppping...");
        await server.stop();
        log.info("Stopped!");
    }
};
