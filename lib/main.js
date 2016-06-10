"use strict";

const co = require("bluebird").coroutine;
const configuration = require("./configuration");
const logger = require("./log");
const log = logger(module);
const apiAuth = require("./apis/auth");
const apiCache = require("./apis/cache");
const apiMetadata = require("./apis/metadata");
const apiTime = require("./apis/time");
const server = require("./http-server");
const process = require("./process");
const tools = require("./tools");

module.exports = {
    start: co(function*(args, version) {
        yield logger.init(args.level);

        log.info("Media Cache Server starting...");

        yield configuration.init(args);
        yield tools.init(configuration);
        yield process.init(configuration);
        yield apiAuth.init(configuration);
        yield apiCache.init(configuration);
        yield apiMetadata.init(configuration);
        yield apiTime.init(configuration);
        yield server.init(configuration, version);

        log.info("Initialization complete.");
    }),
    stop: co(function*() {
        log.info("Received shutdown signal, stoppping...");
        yield server.stop();
    })
};
