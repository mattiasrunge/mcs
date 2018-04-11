"use strict";

const util = require("util");
const http = require("http");
const enableDestroy = require("server-destroy");
const api = require("api.io");
const log = require("./log")(module);

let server;
let params = {};
let connectionSubscription;
let disconnectionSubscription;

module.exports = {
    init: async (config) => {
        params = config;

        server = http.createServer();

        enableDestroy(server);
        server.destroy = util.promisify(server.destroy).bind(server);

        // Socket.io if we have defined API
        await api.start(server);

        connectionSubscription = api.on("connection", (client) => {
            log.info(`Client connected from ${client.handshake.address}`);
        });

        // Subscribe a listener for lost clients
        disconnectionSubscription = api.on("disconnection", (client) => {
            log.info(`Client disconnection from ${client.handshake.address}`);
        });

        // Start to listen for connections
        server.listen(params.port);
    },
    stop: async () => {
        api.off(connectionSubscription);
        api.off(disconnectionSubscription);

        if (server) {
            await api.stop(false);
            await server.destroy();
        }
    }
};
