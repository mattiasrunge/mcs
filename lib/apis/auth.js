"use strict";

const co = require("bluebird").coroutine;
const api = require("api.io");

let params = {};

let auth = api.register("auth", {
    init: co(function*(config) {
        params = config;
    }),
    identify: function*(session, key) {
        if (params.keys.indexOf(key) === -1) {
            return false;
        }

        session.authenticated = true;
        return true;
    }
});

module.exports = auth;
