"use strict";

const util = require("util");
const http = require("http");
const enableDestroy = require("server-destroy");
const api = require("api.io");
const log = require("./log")(module);

class Server {
    constructor() {
        this.server = null;
        this.subscriptions = [];
    }

    async init(config) {
        this.server = http.createServer();

        enableDestroy(this.server);
        this.server.destroy = util.promisify(this.server.destroy).bind(this.server);

        await api.start(this.server);

        this.subscriptions.push(api.on("connection", (client) => {
            log.info(`Client connected from ${client.handshake.address}`);
        }));

        this.subscriptions.push(api.on("disconnection", (client) => {
            log.info(`Client disconnection from ${client.handshake.address}`);
        }));

        this.server.listen(config.port);
    }

    async stop() {
        this.subscriptions.forEach(api.off.bind(api));

        if (this.server) {
            await api.stop(false);
            await this.server.destroy();
        }
    }
}

module.exports = new Server();
