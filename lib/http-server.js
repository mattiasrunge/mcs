"use strict";

const http = require("http");
const co = require("bluebird").coroutine;
const promisify = require("bluebird").promisify;
const koa = require("koa");
const enableDestroy = require("server-destroy");
const api = require("api.io");
const log = require("./log")(module);

let server;
let params = {};
let connectionSubscription;
let disconnectionSubscription;

module.exports = {
    init: co(function*(config, version) {
        params = config;

        let app = koa();

        // Setup application
        app.name = "mcs-v" + version;

        // Configure error handling
        app.use(function*(next) {
            try {
                yield next;
            } catch (error) {
                console.error(error);
                console.error(error.stack);
                this.response.status = error.status || 500;
                this.type = "text/plain";
                this.body = error.message || error;
            }
        });

        // This must come after last app.use()
        server = http.Server(app.callback());

        enableDestroy(server);

        // Socket.io if we have defined API
        yield api.start(server);

        connectionSubscription = api.on("connection", (client) => {
            log.info("Client connected from " + client.handshake.address);
        });

        // Subscribe a listener for lost clients
        disconnectionSubscription = api.on("disconnection", (client) => {
            log.info("Client disconnection from " + client.handshake.address);
        });

        // Start to listen for connections
        server.listen(params.port);
    }),
    stop: co(function*() {
        if (server) {
            api.off(connectionSubscription);
            api.off(disconnectionSubscription);

            yield api.stop();
            yield promisify(server.destroy, { context: server })();
        }
    })
};
