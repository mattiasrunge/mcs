"use strict";

let co = require("bluebird").coroutine;
let configuration = require("./configuration");
let logger = require("./log");
let log = logger(module);
let api = require("./api");
let server = require("./http-server");
let process = require("./process");

module.exports = {
    start: co(function*(args, version) {
        yield logger.init(args.level);
        yield configuration.init(args);
        yield process.init(configuration);
        yield api.init(configuration);
        yield server.init(configuration, version);
    }),
    stop: co(function*() {
        log.info("Received shutdown signal, stoppping...");
        yield server.stop();
    })
};
