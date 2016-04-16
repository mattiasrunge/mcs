"use strict";

const co = require("bluebird").coroutine;
const promisify = require("bluebird").promisify;
const extend = require("extend");
const fs = require("fs");
const readFile = promisify(fs.readFile);
const log = require("./log")(module);

module.exports = {
    init: co(function*(args) {
        log.info("Loading configuration from " + args.config + "...");

        let defaults = JSON.parse(yield readFile(__dirname + "/../conf/defaults.json"));
        let config = JSON.parse(yield readFile(args.config));
        extend(true, module.exports, defaults, config, args);

        return module.exports;
    })
};
