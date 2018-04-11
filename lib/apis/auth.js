"use strict";

const api = require("api.io");

let params = {};

const auth = api.register("auth", {
    init: async (config) => {
        params = config;
    },
    identify: api.export(async (session, key) => {
        if (!params.keys.includes(key)) {
            return false;
        }

        session.authenticated = true;

        return true;
    })
});

module.exports = auth;
