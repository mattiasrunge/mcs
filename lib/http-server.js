"use strict";

const http = require("http");
const co = require("bluebird").coroutine;
const promisify = require("bluebird").promisify;
const koa = require("koa");
const enableDestroy = require("server-destroy");
const api = require("api.io");

let server;
let params = {};

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

        // Start to listen for connections
        server.listen(params.port);
    }),
    stop: co(function*() {
        if (server) {
            yield api.stop();
            yield promisify(server.destroy, { context: server })();
        }
    })
};
